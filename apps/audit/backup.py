"""Sicherung und Rückspielen der Datenbank (Issue 53).

Warum diese App: Eine Sicherung betrifft keine einzelne Fachlichkeit, sondern
den gesamten Datenbestand. Von den vorhandenen Apps bildet `audit` als einzige
keinen Fachbereich ab, sondern ist dafür da, dass die Arbeitszeitnachweise
nachvollziehbar bleiben und erhalten bleiben. Genau dazu gehört die Sicherung:
ohne sie ist der Nachweis nach einem Plattenschaden weg, und Arbeitszeitdaten
unterliegen einer Aufbewahrungspflicht. Eine eigene App nur für zwei Kommandos
ohne Modelle hieße ein App-Label, ein Migrationsverzeichnis und ein Eintrag in
INSTALLED_APPS mehr, ohne dass davon irgendetwas gebraucht würde.

Zwei Wege, je nach Datenbank:

* PostgreSQL wird mit `pg_dump` als gzip-komprimiertes SQL gesichert und mit
  `psql` zurückgespielt.
* SQLite wird mit `VACUUM INTO` gesichert. Ein einfaches Kopieren der Datei
  wäre falsch: wird während des Kopierens geschrieben, ist die Kopie
  unbrauchbar. `VACUUM INTO` schreibt dagegen einen in sich stimmigen Stand.

Geheimnisse tauchen weder in der Kommandozeile noch in der Ausgabe auf: das
Passwort für die PostgreSQL-Werkzeuge kommt über eine kurzlebige Datei
(`PGPASSFILE`), und Fehlermeldungen der Werkzeuge werden vor der Ausgabe
geschwärzt.
"""

import gzip
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic

from django.conf import settings
from django.db import connections
from django.db.utils import ConnectionDoesNotExist
from django.utils import timezone

# Namensschema: zeiterfassung-JJJJMMTT-HHMMSS.<endung>. Der Zeitstempel steht
# in lokaler Zeit im Namen, damit er zu dem passt, was im Journal steht.
FILE_PREFIX = "zeiterfassung-"
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
TIMESTAMP_LENGTH = 15
POSTGRES_SUFFIX = ".sql.gz"
SQLITE_SUFFIX = ".sqlite3"
SUFFIXES = (POSTGRES_SUFFIX, SQLITE_SUFFIX)
# Während des Laufs trägt die Datei diese zusätzliche Endung. Erst am Ende
# wird sie umbenannt; ein abgebrochener Lauf sieht deshalb nie wie eine
# brauchbare Sicherung aus.
PARTIAL_SUFFIX = ".unvollstaendig"


class BackupError(Exception):
    """Sichern oder Rückspielen ist fehlgeschlagen."""


@dataclass(frozen=True)
class BackupResult:
    """Was bei einer Sicherung herausgekommen ist."""

    path: Path
    size: int
    seconds: float


# --- Konfiguration ----------------------------------------------------------


def database_config(alias: str = "default") -> dict:
    """Die Einstellungen der gewünschten Datenbank."""
    try:
        return settings.DATABASES[alias]
    except KeyError:
        raise BackupError(f"Unbekannte Datenbank: {alias}.") from None


def is_postgres(config: dict) -> bool:
    return "postgresql" in config.get("ENGINE", "")


def is_sqlite(config: dict) -> bool:
    return "sqlite" in config.get("ENGINE", "")


def describe(config: dict) -> str:
    """Kurze Beschreibung der Datenbank für die Ausgabe, ohne Passwort."""
    name = _database_name(config)
    if is_postgres(config):
        host = config.get("HOST") or ""
        port = config.get("PORT") or ""
        where = f"{host}:{port}" if host and port else (host or "lokaler Socket")
        return f'PostgreSQL "{name}" auf {where}'
    if is_sqlite(config):
        return f"SQLite ({name})"
    return f"{config.get('ENGINE', 'unbekannt')} ({name})"


def backup_directory(override: str | os.PathLike | None = None) -> Path:
    """Das Zielverzeichnis, absolut aufgelöst."""
    directory = Path(override) if override else Path(settings.BACKUP_DIR)
    return directory.expanduser().resolve()


def _database_name(config: dict) -> str:
    name = str(config.get("NAME") or "")
    if not name:
        raise BackupError("In DATABASES ist kein NAME eingetragen.")
    return name


def _binary(name: str) -> str:
    """Sucht ein Programm im PATH und liefert den vollständigen Pfad.

    Der volle Pfad statt des blanken Namens, damit nicht zufällig etwas
    anderes aus dem PATH gestartet wird.
    """
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(name).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    raise BackupError(
        f"{name} wurde nicht gefunden. Die PostgreSQL-Werkzeuge müssen dort "
        "verfügbar sein, wo das Kommando läuft (BACKUP_PG_DUMP, BACKUP_PSQL)."
    )


# --- Aufrufe der PostgreSQL-Werkzeuge ---------------------------------------


def _connection_arguments(config: dict) -> list[str]:
    arguments: list[str] = []
    host = str(config.get("HOST") or "")
    port = str(config.get("PORT") or "")
    user = str(config.get("USER") or "")
    if host:
        arguments += ["--host", host]
    if port:
        arguments += ["--port", port]
    if user:
        arguments += ["--username", user]
    return arguments


def pg_dump_command(config: dict) -> list[str]:
    """Der Aufruf für die Sicherung.

    `--no-password` sorgt dafür, dass niemals nach einem Passwort gefragt
    wird: ein Lauf aus einem Timer oder Cronjob soll scheitern statt zu
    hängen. Das Passwort selbst steht bewusst nicht in der Liste, es kommt
    über PGPASSFILE; in der Prozessliste ist es damit nicht zu sehen.
    """
    command = [
        _binary(settings.BACKUP_PG_DUMP),
        "--no-password",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
    ]
    command += _connection_arguments(config)
    command.append(_database_name(config))
    return command


def psql_command(config: dict) -> list[str]:
    """Der Aufruf fürs Rückspielen.

    `ON_ERROR_STOP` und `--single-transaction`: entweder ist am Ende der
    ganze Stand da oder gar nichts. Ein halb eingespielter Dump wäre
    schlimmer als der kaputte Stand davor.
    """
    command = [
        _binary(settings.BACKUP_PSQL),
        "--no-password",
        "--quiet",
        "--set",
        "ON_ERROR_STOP=on",
        "--single-transaction",
    ]
    command += _connection_arguments(config)
    command += ["--dbname", _database_name(config)]
    return command


def _pgpass_line(config: dict, password: str) -> str:
    fields = [
        str(config.get("HOST") or "*"),
        str(config.get("PORT") or "*"),
        _database_name(config),
        str(config.get("USER") or "*"),
        password,
    ]
    # Im .pgpass-Format trennt der Doppelpunkt; ein Doppelpunkt oder
    # Rückstrich im Wert wird mit Rückstrich geschützt.
    return ":".join(field.replace("\\", "\\\\").replace(":", "\\:") for field in fields)


@contextmanager
def postgres_environment(config: dict):
    """Die Umgebung für pg_dump und psql, mit dem Passwort in einer Datei.

    Nicht über die Kommandozeile: die steht in der Prozessliste und ist für
    jeden auf dem Rechner lesbar. Nicht über PGPASSWORD: eine
    Umgebungsvariable wird an jeden Kindprozess vererbt und steht in
    `/proc/<pid>/environ`. Die Datei hat nur Rechte für den eigenen Benutzer
    und wird danach wieder gelöscht.
    """
    environment = os.environ.copy()
    environment.pop("PGPASSWORD", None)
    password = str(config.get("PASSWORD") or "")
    if not password:
        yield environment
        return

    handle, name = tempfile.mkstemp(prefix="zeiterfassung-pgpass-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(_pgpass_line(config, password) + "\n")
        environment["PGPASSFILE"] = name
        yield environment
    finally:
        Path(name).unlink(missing_ok=True)


def redact(text: str, config: dict) -> str:
    """Nimmt das Passwort aus einer Meldung, bevor sie ausgegeben wird."""
    password = str(config.get("PASSWORD") or "")
    if password:
        text = text.replace(password, "***")
    return text


def _tool_failed(tool: str, code: int, message: str, config: dict) -> BackupError:
    text = f"{tool} ist mit Rückgabewert {code} fehlgeschlagen."
    message = redact(message, config).strip()
    if message:
        text = f"{text}\n{message}"
    return BackupError(text)


# --- Sichern ----------------------------------------------------------------


def _prepare_directory(directory: Path) -> None:
    try:
        # 0700: eine Sicherung enthält sämtliche personenbezogenen Daten und
        # geht niemanden sonst etwas an.
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as error:
        raise BackupError(f"Verzeichnis lässt sich nicht anlegen: {error}") from error
    if not os.access(directory, os.W_OK):
        raise BackupError(f"Verzeichnis ist nicht beschreibbar: {directory}")


def _free_target(directory: Path, config: dict, now: datetime | None = None) -> Path:
    """Ein noch freier Dateiname mit Zeitstempel."""
    moment = now or timezone.localtime()
    suffix = POSTGRES_SUFFIX if is_postgres(config) else SQLITE_SUFFIX
    stamp = moment.strftime(TIMESTAMP_FORMAT)
    target = directory / f"{FILE_PREFIX}{stamp}{suffix}"
    # Zwei Läufe in derselben Sekunde sollen sich nicht gegenseitig
    # überschreiben.
    counter = 1
    while target.exists():
        target = directory / f"{FILE_PREFIX}{stamp}-{counter}{suffix}"
        counter += 1
    return target


def create_backup(
    *,
    alias: str = "default",
    directory: str | os.PathLike | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Schreibt eine Sicherung und liefert Pfad, Größe und Dauer."""
    config = database_config(alias)
    target_directory = backup_directory(directory)
    _prepare_directory(target_directory)
    target = _free_target(target_directory, config, now)
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    partial.unlink(missing_ok=True)

    started = monotonic()
    try:
        if is_postgres(config):
            _dump_postgres(config, partial)
        elif is_sqlite(config):
            _dump_sqlite(config, partial)
        else:
            raise BackupError(
                "Gesichert werden können nur PostgreSQL und SQLite, "
                f"nicht {config.get('ENGINE', 'unbekannt')}."
            )
        os.chmod(partial, 0o600)
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    seconds = monotonic() - started

    return BackupResult(path=target, size=target.stat().st_size, seconds=seconds)


def _dump_postgres(config: dict, target: Path) -> None:
    command = pg_dump_command(config)
    with postgres_environment(config) as environment, tempfile.TemporaryFile() as errors:
        with gzip.open(target, "wb") as out:
            # Ohne Shell und mit fester Argumentliste: nichts aus der
            # Konfiguration wird je von einer Shell interpretiert.
            process = subprocess.Popen(  # noqa: S603
                command,
                stdout=subprocess.PIPE,
                stderr=errors,
                stdin=subprocess.DEVNULL,
                env=environment,
            )
            # Die Ausgabe wird stückweise in die gzip-Datei geschoben, damit
            # auch ein großer Dump nicht im Speicher landet. stderr geht in
            # eine Datei und nicht in eine Pipe, sonst könnten sich beide
            # Seiten gegenseitig blockieren.
            with process.stdout as stream:
                shutil.copyfileobj(stream, out)
            code = process.wait()
        errors.seek(0)
        message = errors.read().decode("utf-8", "replace")
    if code != 0:
        raise _tool_failed("pg_dump", code, message, config)


def _dump_sqlite(config: dict, target: Path) -> None:
    source = Path(_database_name(config))
    if not source.is_file():
        raise BackupError(f"Die SQLite-Datei gibt es nicht: {source}")
    connection = sqlite3.connect(str(source), timeout=30, isolation_level=None)
    try:
        # VACUUM INTO nimmt einen stimmigen Stand auf, auch wenn nebenbei
        # geschrieben wird, und schreibt ihn ohne Lücken in die Zieldatei.
        connection.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error as error:
        raise BackupError(f"SQLite-Sicherung fehlgeschlagen: {error}") from error
    finally:
        connection.close()


# --- Aufräumen --------------------------------------------------------------


def backup_files(directory: str | os.PathLike) -> list[Path]:
    """Alle Sicherungen im Verzeichnis, neueste zuerst.

    Bewusst eng gefasst: nur gewöhnliche Dateien unmittelbar in diesem
    Verzeichnis, deren Name mit unserem Präfix beginnt und auf eine bekannte
    Endung endet. Verweise, Unterverzeichnisse und angefangene Läufe bleiben
    außen vor, damit das Aufräumen nichts anfasst, was ihm nicht gehört.
    """
    path = Path(directory)
    if not path.is_dir():
        return []
    found = [
        entry
        for entry in path.iterdir()
        if entry.name.startswith(FILE_PREFIX)
        and entry.name.endswith(SUFFIXES)
        and not entry.is_symlink()
        and entry.is_file()
    ]
    return sorted(found, key=lambda entry: entry.name, reverse=True)


def _stamp(path: Path) -> datetime | None:
    text = path.name[len(FILE_PREFIX) : len(FILE_PREFIX) + TIMESTAMP_LENGTH]
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def cleanup(
    directory: str | os.PathLike,
    *,
    keep: int | None = None,
    keep_days: int | None = None,
    now: datetime | None = None,
) -> list[Path]:
    """Entfernt alte Sicherungen und liefert, was entfernt wurde.

    Zwei Grenzen, die sich ergänzen: `keep` begrenzt die Anzahl, `keep_days`
    das Alter. 0 schaltet die jeweilige Grenze ab. Die neueste Sicherung
    bleibt in jedem Fall liegen, sonst könnte eine zu knapp eingestellte
    Altersgrenze den letzten Stand wegräumen.
    """
    keep = settings.BACKUP_KEEP if keep is None else keep
    keep_days = settings.BACKUP_KEEP_DAYS if keep_days is None else keep_days
    moment = now or timezone.localtime().replace(tzinfo=None)

    removed: list[Path] = []
    for index, path in enumerate(backup_files(directory)):
        if index == 0:
            continue
        stamp = _stamp(path)
        too_many = keep > 0 and index >= keep
        too_old = keep_days > 0 and stamp is not None and moment - stamp > timedelta(days=keep_days)
        if too_many or too_old:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def latest_backup(directory: str | os.PathLike) -> Path | None:
    """Die neueste Sicherung im Verzeichnis, falls es eine gibt."""
    found = backup_files(directory)
    return found[0] if found else None


# --- Rückspielen ------------------------------------------------------------


def restore(path: str | os.PathLike, *, alias: str = "default") -> float:
    """Spielt eine Sicherung zurück und liefert die gebrauchte Zeit."""
    config = database_config(alias)
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise BackupError(f"Die Datei gibt es nicht: {source}")

    # Eine offene Verbindung würde beim SQLite-Weg auf die alte Datei zeigen.
    try:
        connections[alias].close()
    except ConnectionDoesNotExist:
        pass

    started = monotonic()
    if is_postgres(config):
        if not source.name.endswith(POSTGRES_SUFFIX):
            raise BackupError(
                f"Für PostgreSQL wird eine Datei mit der Endung {POSTGRES_SUFFIX} gebraucht."
            )
        _restore_postgres(config, source)
    elif is_sqlite(config):
        if not source.name.endswith(SQLITE_SUFFIX):
            raise BackupError(
                f"Für SQLite wird eine Datei mit der Endung {SQLITE_SUFFIX} gebraucht."
            )
        _restore_sqlite(config, source)
    else:
        raise BackupError(
            "Zurückgespielt werden können nur PostgreSQL und SQLite, "
            f"nicht {config.get('ENGINE', 'unbekannt')}."
        )
    return monotonic() - started


def _restore_postgres(config: dict, source: Path) -> None:
    command = psql_command(config)
    with postgres_environment(config) as environment, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            env=environment,
        )
        broken = False
        try:
            with gzip.open(source, "rb") as stream, process.stdin as target:
                shutil.copyfileobj(stream, target)
        except BrokenPipeError:
            # psql hat vorzeitig aufgegeben; warum, steht in stderr.
            broken = True
        code = process.wait()
        errors.seek(0)
        message = errors.read().decode("utf-8", "replace")
    if broken and code == 0:
        raise BackupError(
            "psql hat die Eingabe vorzeitig geschlossen; die Sicherung wurde "
            "nicht vollständig eingespielt.\n" + redact(message, config).strip()
        )
    if code != 0:
        raise _tool_failed("psql", code, message, config)


def _restore_sqlite(config: dict, source: Path) -> None:
    target = Path(_database_name(config))
    _check_sqlite(source)

    mode = target.stat().st_mode & 0o777 if target.is_file() else 0o600
    temporary = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, mode)
        # Ein liegengebliebenes Write-Ahead-Log der alten Datenbank würde
        # sich sonst über den zurückgespielten Stand legen.
        for extra in ("-wal", "-shm"):
            Path(str(target) + extra).unlink(missing_ok=True)
        os.replace(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise BackupError(f"Rückspielen fehlgeschlagen: {error}") from error


def _check_sqlite(source: Path) -> None:
    """Prüft vor dem Überschreiben, dass die Sicherung überhaupt brauchbar ist."""
    try:
        connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as error:
        raise BackupError(f"Die Sicherung lässt sich nicht öffnen: {error}") from error
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as error:
        raise BackupError(f"Die Datei ist keine SQLite-Datenbank: {error}") from error
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise BackupError("Die Sicherung ist beschädigt und wird nicht zurückgespielt.")


# --- Ausgabe ----------------------------------------------------------------


def format_size(size: int) -> str:
    """Größe in einer Einheit, die sich lesen lässt."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} B"
    return f"{value:.1f}".replace(".", ",") + f" {unit}"


def format_seconds(seconds: float) -> str:
    """Dauer in Sekunden, mit Komma wie im Deutschen üblich."""
    return f"{seconds:.1f}".replace(".", ",") + " s"


def format_count(count: int) -> str:
    """Singular und Plural für die Zahl der entfernten Sicherungen."""
    if count == 1:
        return "eine alte Sicherung"
    return f"{count} alte Sicherungen"
