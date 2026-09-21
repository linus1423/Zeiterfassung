"""Sicherung und Rückspielen der Datenbank (Issue 53).

Die Tests kommen ohne echten PostgreSQL-Server aus: der SQLite-Weg wird
wirklich ausgeführt, der PostgreSQL-Weg über den zusammengebauten Aufruf und
einen abgefangenen Unterprozess geprüft.
"""

import gzip
import io
import sqlite3
import subprocess
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.audit import backup

POSTGRES_CONFIG = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "zeiterfassung",
    "USER": "zeiterfassung",
    "PASSWORD": "sehr-geheim",  # noqa: S106
    "HOST": "zeiterfassung-db",
    "PORT": "5432",
}


# --- Hilfsmittel ------------------------------------------------------------


def _sqlite_database(path: Path, *names: str) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS zeiten (name TEXT)")
        connection.executemany("INSERT INTO zeiten (name) VALUES (?)", [(n,) for n in names])
        connection.commit()
    finally:
        connection.close()


def _names(path: Path) -> list[str]:
    connection = sqlite3.connect(str(path))
    try:
        return [row[0] for row in connection.execute("SELECT name FROM zeiten ORDER BY name")]
    finally:
        connection.close()


def _fake_backup(directory: Path, stamp: str, suffix: str = backup.POSTGRES_SUFFIX) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{backup.FILE_PREFIX}{stamp}{suffix}"
    path.write_bytes(b"nur ein Platzhalter")
    return path


def _stamp(days_ago: int, now: datetime) -> str:
    return (now - timedelta(days=days_ago)).strftime(backup.TIMESTAMP_FORMAT)


class _KeepingBytesIO(io.BytesIO):
    """BytesIO, das seinen Inhalt auch nach dem Schließen noch hergibt."""

    value = b""

    def close(self):
        if not self.closed:
            self.value = self.getvalue()
        super().close()


class _FakeProcess:
    """Stellt pg_dump oder psql dar, ohne dass eines davon vorhanden sein muss."""

    def __init__(self, *, payload, code, message, stdout, stderr, stdin):
        self.returncode = code
        self._code = code
        self.stdout = io.BytesIO(payload) if stdout == subprocess.PIPE else None
        self.stdin = _KeepingBytesIO() if stdin == subprocess.PIPE else None
        if message:
            stderr.write(message)

    def wait(self):
        return self._code


def _intercept(monkeypatch, *, payload=b"-- Dump\n", code=0, message=b""):
    """Fängt den Aufruf von pg_dump bzw. psql ab und schreibt mit, was ankam."""
    seen = []

    def factory(command, stdout=None, stderr=None, stdin=None, env=None):
        process = _FakeProcess(
            payload=payload, code=code, message=message, stdout=stdout, stderr=stderr, stdin=stdin
        )
        seen.append({"command": command, "env": env, "process": process})
        return process

    monkeypatch.setattr(backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(backup.subprocess, "Popen", factory)
    return seen


@pytest.fixture
def sqlite_database(tmp_path, monkeypatch):
    """Eine echte kleine SQLite-Datenbank als zusätzlicher DATABASES-Eintrag."""
    path = tmp_path / "zeiten.sqlite3"
    _sqlite_database(path, "Mia")
    monkeypatch.setitem(
        settings.DATABASES,
        "sicherung",
        {"ENGINE": "django.db.backends.sqlite3", "NAME": str(path)},
    )
    return path


@pytest.fixture
def postgres_database(monkeypatch):
    monkeypatch.setitem(settings.DATABASES, "sicherung", dict(POSTGRES_CONFIG))
    return POSTGRES_CONFIG


# --- SQLite sichern ---------------------------------------------------------


def test_the_sqlite_backup_is_a_usable_database(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"
    output = StringIO()

    call_command("backup_database", "--database", "sicherung", "--dir", str(target), stdout=output)

    found = backup.backup_files(target)
    assert len(found) == 1
    assert found[0].name.endswith(backup.SQLITE_SUFFIX)
    assert _names(found[0]) == ["Mia"]
    text = output.getvalue()
    assert str(found[0]) in text
    assert "Größe" in text and "Dauer" in text


def test_the_backup_is_only_readable_by_its_owner(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"

    call_command(
        "backup_database", "--database", "sicherung", "--dir", str(target), stdout=StringIO()
    )

    assert target.stat().st_mode & 0o777 == 0o700
    assert backup.backup_files(target)[0].stat().st_mode & 0o777 == 0o600


def test_two_runs_do_not_overwrite_each_other(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"
    moment = datetime(2026, 9, 21, 3, 15, 0)

    backup.create_backup(alias="sicherung", directory=target, now=moment)
    backup.create_backup(alias="sicherung", directory=target, now=moment)

    assert len(backup.backup_files(target)) == 2


def test_a_failed_run_leaves_no_half_written_file(tmp_path, monkeypatch):
    monkeypatch.setitem(
        settings.DATABASES,
        "sicherung",
        {"ENGINE": "django.db.backends.sqlite3", "NAME": str(tmp_path / "gibtsnicht.sqlite3")},
    )
    target = tmp_path / "sicherungen"

    with pytest.raises(CommandError):
        call_command("backup_database", "--database", "sicherung", "--dir", str(target))

    assert list(target.iterdir()) == []


def test_another_database_engine_is_refused(tmp_path, monkeypatch):
    monkeypatch.setitem(
        settings.DATABASES,
        "sicherung",
        {"ENGINE": "django.db.backends.oracle", "NAME": "zeit"},
    )

    with pytest.raises(CommandError, match="PostgreSQL und SQLite"):
        call_command("backup_database", "--database", "sicherung", "--dir", str(tmp_path / "ziel"))


# --- PostgreSQL sichern -----------------------------------------------------


def test_the_pg_dump_call_carries_no_password(monkeypatch):
    monkeypatch.setattr(backup.shutil, "which", lambda name: f"/usr/bin/{name}")

    command = backup.pg_dump_command(POSTGRES_CONFIG)

    assert command[0] == "/usr/bin/pg_dump"
    assert "--no-password" in command
    assert "-W" not in command and "--password" not in command
    assert all(POSTGRES_CONFIG["PASSWORD"] not in part for part in command)
    assert command[-1] == "zeiterfassung"
    assert "--host" in command and "zeiterfassung-db" in command


def test_the_password_only_travels_in_a_file():
    with backup.postgres_environment(POSTGRES_CONFIG) as environment:
        assert "PGPASSWORD" not in environment
        path = Path(environment["PGPASSFILE"])
        assert path.stat().st_mode & 0o777 == 0o600
        assert POSTGRES_CONFIG["PASSWORD"] in path.read_text(encoding="utf-8")

    assert not path.exists()


def test_colons_in_the_password_are_escaped():
    config = dict(POSTGRES_CONFIG, PASSWORD="a:b\\c")

    line = backup._pgpass_line(config, config["PASSWORD"])

    assert line.endswith("a\\:b\\\\c")
    assert line.count(":") == 4 + 1  # vier Trenner plus der geschützte im Wert


def test_the_postgres_backup_writes_a_gzip_file(postgres_database, tmp_path, monkeypatch):
    seen = _intercept(monkeypatch, payload=b"-- Dump der Zeiterfassung\n")
    target = tmp_path / "sicherungen"
    output = StringIO()

    call_command("backup_database", "--database", "sicherung", "--dir", str(target), stdout=output)

    found = backup.backup_files(target)
    assert len(found) == 1
    assert found[0].name.endswith(backup.POSTGRES_SUFFIX)
    with gzip.open(found[0], "rb") as stream:
        assert stream.read() == b"-- Dump der Zeiterfassung\n"
    assert seen[0]["command"][0] == "/usr/bin/pg_dump"
    assert "PGPASSFILE" in seen[0]["env"]
    assert POSTGRES_CONFIG["PASSWORD"] not in output.getvalue()


def test_a_failing_pg_dump_ends_with_an_error(postgres_database, tmp_path, monkeypatch):
    _intercept(monkeypatch, code=2, message=b"FATAL: Verbindung abgelehnt\n")
    target = tmp_path / "sicherungen"

    with pytest.raises(CommandError) as error:
        call_command("backup_database", "--database", "sicherung", "--dir", str(target))

    assert "pg_dump" in str(error.value)
    assert "Verbindung abgelehnt" in str(error.value)
    assert backup.backup_files(target) == []


def test_a_password_in_an_error_message_is_blacked_out(postgres_database, tmp_path, monkeypatch):
    _intercept(monkeypatch, code=1, message=b'FATAL: Passwort "sehr-geheim" falsch\n')

    with pytest.raises(CommandError) as error:
        call_command("backup_database", "--database", "sicherung", "--dir", str(tmp_path / "ziel"))

    assert POSTGRES_CONFIG["PASSWORD"] not in str(error.value)
    assert "***" in str(error.value)


def test_missing_tools_are_named(postgres_database, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.shutil, "which", lambda name: None)

    with pytest.raises(CommandError, match="pg_dump"):
        call_command("backup_database", "--database", "sicherung", "--dir", str(tmp_path / "ziel"))


# --- Aufräumen --------------------------------------------------------------


def test_only_the_newest_backups_are_kept(tmp_path):
    directory = tmp_path / "sicherungen"
    now = datetime(2026, 9, 21, 12, 0, 0)
    for days in (0, 1, 2, 3, 4):
        _fake_backup(directory, _stamp(days, now))

    removed = backup.cleanup(directory, keep=2, keep_days=0, now=now)

    assert len(removed) == 3
    remaining = [path.name for path in backup.backup_files(directory)]
    assert remaining == [
        f"{backup.FILE_PREFIX}{_stamp(0, now)}{backup.POSTGRES_SUFFIX}",
        f"{backup.FILE_PREFIX}{_stamp(1, now)}{backup.POSTGRES_SUFFIX}",
    ]


def test_old_backups_go_by_age(tmp_path):
    directory = tmp_path / "sicherungen"
    now = datetime(2026, 9, 21, 12, 0, 0)
    fresh = _fake_backup(directory, _stamp(1, now))
    old = _fake_backup(directory, _stamp(40, now))
    _fake_backup(directory, _stamp(0, now))

    removed = backup.cleanup(directory, keep=0, keep_days=30, now=now)

    assert removed == [old]
    assert fresh.exists()


def test_the_newest_backup_is_never_removed(tmp_path):
    directory = tmp_path / "sicherungen"
    now = datetime(2026, 9, 21, 12, 0, 0)
    newest = _fake_backup(directory, _stamp(100, now))
    older = _fake_backup(directory, _stamp(200, now))

    removed = backup.cleanup(directory, keep=1, keep_days=1, now=now)

    assert removed == [older]
    assert newest.exists()


def test_cleanup_leaves_foreign_files_alone(tmp_path):
    directory = tmp_path / "sicherungen"
    now = datetime(2026, 9, 21, 12, 0, 0)
    _fake_backup(directory, _stamp(0, now))
    _fake_backup(directory, _stamp(1, now))
    fremd = directory / "wichtig.sql.gz"
    fremd.write_bytes(b"nicht anfassen")
    notiz = directory / f"{backup.FILE_PREFIX}20260101-000000.txt"
    notiz.write_bytes(b"auch nicht")
    ordner = directory / f"{backup.FILE_PREFIX}20260101-000000{backup.SQLITE_SUFFIX}"
    ordner.mkdir()

    backup.cleanup(directory, keep=1, keep_days=0, now=now)

    assert fremd.exists() and notiz.exists() and ordner.is_dir()


def test_cleanup_runs_with_the_command(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"
    now = datetime(2026, 9, 21, 12, 0, 0)
    for days in (1, 2, 3):
        _fake_backup(target, _stamp(days, now), backup.SQLITE_SUFFIX)
    output = StringIO()

    call_command(
        "backup_database",
        "--database",
        "sicherung",
        "--dir",
        str(target),
        "--keep",
        "2",
        stdout=output,
    )

    assert len(backup.backup_files(target)) == 2
    assert "Aufgeräumt: 2 alte Sicherungen entfernt." in output.getvalue()


# --- Rückspielen ------------------------------------------------------------


def test_the_round_trip_brings_the_data_back(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"
    call_command(
        "backup_database", "--database", "sicherung", "--dir", str(target), stdout=StringIO()
    )
    _sqlite_database(sqlite_database, "Versehen")
    assert _names(sqlite_database) == ["Mia", "Versehen"]

    call_command(
        "restore_database",
        "--database",
        "sicherung",
        "--dir",
        str(target),
        "--noinput",
        stdout=StringIO(),
    )

    assert _names(sqlite_database) == ["Mia"]


def test_without_confirmation_nothing_is_overwritten(sqlite_database, tmp_path, monkeypatch):
    target = tmp_path / "sicherungen"
    call_command(
        "backup_database", "--database", "sicherung", "--dir", str(target), stdout=StringIO()
    )
    _sqlite_database(sqlite_database, "Versehen")
    monkeypatch.setattr("builtins.input", lambda prompt="": "nein")
    output = StringIO()

    call_command("restore_database", "--database", "sicherung", "--dir", str(target), stdout=output)

    assert "Abgebrochen" in output.getvalue()
    assert _names(sqlite_database) == ["Mia", "Versehen"]


def test_a_confirmed_restore_runs(sqlite_database, tmp_path, monkeypatch):
    target = tmp_path / "sicherungen"
    call_command(
        "backup_database", "--database", "sicherung", "--dir", str(target), stdout=StringIO()
    )
    _sqlite_database(sqlite_database, "Versehen")
    monkeypatch.setattr("builtins.input", lambda prompt="": " Ja ")

    call_command(
        "restore_database", "--database", "sicherung", "--dir", str(target), stdout=StringIO()
    )

    assert _names(sqlite_database) == ["Mia"]


def test_a_stale_write_ahead_log_is_removed(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"
    call_command(
        "backup_database", "--database", "sicherung", "--dir", str(target), stdout=StringIO()
    )
    wal = Path(str(sqlite_database) + "-wal")
    wal.write_bytes(b"alter Rest")

    call_command(
        "restore_database",
        "--database",
        "sicherung",
        "--dir",
        str(target),
        "--noinput",
        stdout=StringIO(),
    )

    assert not wal.exists()


def test_a_broken_file_is_not_restored(sqlite_database, tmp_path):
    target = tmp_path / "sicherungen"
    target.mkdir()
    kaputt = target / f"{backup.FILE_PREFIX}20260921-031500{backup.SQLITE_SUFFIX}"
    kaputt.write_bytes(b"das ist keine Datenbank")

    with pytest.raises(CommandError, match="SQLite"):
        call_command(
            "restore_database", "--database", "sicherung", "--dir", str(target), "--noinput"
        )

    assert _names(sqlite_database) == ["Mia"]


def test_without_a_backup_there_is_a_clear_message(sqlite_database, tmp_path):
    with pytest.raises(CommandError, match="keine Sicherung"):
        call_command(
            "restore_database",
            "--database",
            "sicherung",
            "--dir",
            str(tmp_path / "leer"),
            "--noinput",
        )


def test_the_postgres_restore_feeds_psql(postgres_database, tmp_path, monkeypatch):
    target = tmp_path / "sicherungen"
    target.mkdir()
    datei = target / f"{backup.FILE_PREFIX}20260921-031500{backup.POSTGRES_SUFFIX}"
    with gzip.open(datei, "wb") as stream:
        stream.write(b"-- Dump der Zeiterfassung\n")
    seen = _intercept(monkeypatch)
    output = StringIO()

    call_command(
        "restore_database",
        "--database",
        "sicherung",
        "--dir",
        str(target),
        "--noinput",
        stdout=output,
    )

    assert seen[0]["command"][0] == "/usr/bin/psql"
    assert "ON_ERROR_STOP=on" in seen[0]["command"]
    assert "--single-transaction" in seen[0]["command"]
    assert seen[0]["process"].stdin.value == b"-- Dump der Zeiterfassung\n"
    assert POSTGRES_CONFIG["PASSWORD"] not in output.getvalue()


def test_a_failing_psql_ends_with_an_error(postgres_database, tmp_path, monkeypatch):
    target = tmp_path / "sicherungen"
    target.mkdir()
    datei = target / f"{backup.FILE_PREFIX}20260921-031500{backup.POSTGRES_SUFFIX}"
    with gzip.open(datei, "wb") as stream:
        stream.write(b"-- Dump\n")
    _intercept(monkeypatch, code=3, message=b"FEHLER: Relation existiert bereits\n")

    with pytest.raises(CommandError) as error:
        call_command(
            "restore_database", "--database", "sicherung", "--dir", str(target), "--noinput"
        )

    assert "psql" in str(error.value)
    assert "Relation existiert bereits" in str(error.value)


def test_a_sqlite_file_is_refused_for_postgres(postgres_database, tmp_path, monkeypatch):
    _intercept(monkeypatch)
    datei = tmp_path / f"{backup.FILE_PREFIX}20260921-031500{backup.SQLITE_SUFFIX}"
    datei.write_bytes(b"egal")

    with pytest.raises(CommandError, match=r"\.sql\.gz"):
        call_command(
            "restore_database",
            "--database",
            "sicherung",
            "--file",
            str(datei),
            "--noinput",
        )


# --- Ausgabe ----------------------------------------------------------------


def test_sizes_are_written_the_german_way():
    assert backup.format_size(512) == "512 B"
    assert backup.format_size(2048) == "2,0 KiB"
    assert backup.format_size(5 * 1024 * 1024) == "5,0 MiB"


def test_the_description_never_shows_the_password():
    text = backup.describe(POSTGRES_CONFIG)

    assert "zeiterfassung-db:5432" in text
    assert POSTGRES_CONFIG["PASSWORD"] not in text
