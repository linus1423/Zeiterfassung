"""Zeiten aus CSV importieren (Issue 54).

Alle Zeiten sind fest verankert (Februar 2025) und nie „jetzt minus etwas“:
sonst liefe ein Test nachts über Mitternacht und die Summen stimmten nicht.
"""

from datetime import date, datetime, time, timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups.models import Activity, GroupMembership, PeriodLock
from apps.tracking import csv_import
from apps.tracking.models import BreakEntry, TimeEntry

HEADER = "Personalnummer;E-Mail;Gruppe;Tätigkeit;Datum;Beginn;Ende;Pausen;Notiz"

DAY = date(2025, 2, 3)


def csv_bytes(*rows: str, header: str = HEADER, encoding: str = "utf-8-sig") -> bytes:
    return ("\r\n".join([header, *rows]) + "\r\n").encode(encoding)


def row(
    *,
    number: str = "",
    email: str = "mitglied@example.com",
    group: str = "Werkstatt",
    activity: str = "Montage",
    day: str = "03.02.2025",
    start: str = "08:00",
    end: str = "16:00",
    breaks: str = "",
    note: str = "",
) -> str:
    return f"{number};{email};{group};{activity};{day};{start};{end};{breaks};{note}"


def at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def numbered_member(member):
    member.personnel_number = "1001"
    member.save(update_fields=["personnel_number"])
    return member


# --- gute Datei -------------------------------------------------------------


def test_good_file_creates_entries(member, group, activity):
    data = csv_bytes(row(breaks="30", note="Aus dem Altsystem"), row(start="17:00", end="19:00"))

    result = csv_import.run(data)

    assert result.count == 2
    entries = list(TimeEntry.objects.filter(user=member).order_by("start"))
    assert [entry.source for entry in entries] == [TimeEntry.Source.IMPORT] * 2
    assert entries[0].group == group
    assert entries[0].activity == activity
    assert entries[0].note == "Aus dem Altsystem"
    assert entries[0].start == at(DAY, 8)
    assert entries[0].end == at(DAY, 16)
    # 30 Minuten Pause, mittig in acht Stunden Anwesenheit.
    pause = entries[0].breaks.get()
    assert pause.duration == timedelta(minutes=30)
    assert pause.start == at(DAY, 11, 45)
    assert entries[0].duration == timedelta(hours=7, minutes=30)


def test_identification_by_personnel_number(numbered_member, group, activity):
    data = csv_bytes(row(number="1001", email=""))

    csv_import.run(data)

    assert TimeEntry.objects.filter(user=numbered_member).count() == 1


def test_explicit_breaks_and_night_shift(member, group, activity):
    data = csv_bytes(row(start="22:00", end="06:00", breaks="01:00-01:30, 03:00-03:15"))

    csv_import.run(data)

    entry = TimeEntry.objects.get()
    assert entry.start == at(DAY, 22)
    assert entry.end == at(DAY + timedelta(days=1), 6)
    assert entry.break_duration == timedelta(minutes=45)
    assert list(entry.breaks.values_list("start", flat=True)) == [
        at(DAY + timedelta(days=1), 1),
        at(DAY + timedelta(days=1), 3),
    ]


@pytest.mark.parametrize(
    ("written", "expected"), [("45", 45), ("0:45", 45), ("1:00", 60), ("", 0), ("0", 0)]
)
def test_break_durations_may_be_written_either_way(member, group, activity, written, expected):
    csv_import.run(csv_bytes(row(breaks=written)))

    entry = TimeEntry.objects.get()
    assert entry.break_duration == timedelta(minutes=expected)


def test_full_datetimes_and_comma_delimiter(member, group, activity):
    header = "E-Mail,Gruppe,Tätigkeit,Beginn,Ende"
    data = csv_bytes(
        "mitglied@example.com,Werkstatt,Montage,03.02.2025 08:00,03.02.2025 12:00",
        header=header,
    )

    csv_import.run(data)

    entry = TimeEntry.objects.get()
    assert entry.start == at(DAY, 8)
    assert entry.end == at(DAY, 12)


def test_names_are_matched_forgivingly(member, group, activity):
    """Aus dem Altsystem kommen die Namen selten bis aufs Zeichen genau."""
    data = csv_bytes(row(group="werkstatt", activity="MONTAGE"))

    plan = csv_import.prepare(data)

    assert plan.ok
    assert plan.entries[0].group == group
    assert plan.entries[0].activity == activity


def test_ambiguous_group_names_are_refused(member, group, activity):
    """Wo der entschärfte Name auf zwei Gruppen passt, wird nicht geraten."""
    from apps.groups.models import Group

    Group.objects.create(name="Werk-Statt", slug="werk-statt")
    data = csv_bytes(row(group="WERK-STATT"), row(group="Werkstatt"))

    plan = csv_import.prepare(data)

    problems = messages_by_line(plan)
    assert "passt auf mehrere Gruppen" in problems[2]
    # Der genaue Name bleibt eindeutig und wird weiter zugeordnet.
    assert 3 not in problems


def test_windows_encoding_is_read(member, group, activity):
    # Der Kopf trägt Umlaute ("Tätigkeit"), daran hängt die Erkennung.
    data = csv_bytes(row(), encoding="cp1252")

    plan = csv_import.prepare(data)

    assert plan.ok
    assert plan.entries[0].activity == activity


def test_a_row_without_an_activity_is_refused(member, group, activity):
    """Ohne Tätigkeit entsteht kein Eintrag mehr (Issue 83)."""
    plan = csv_import.prepare(csv_bytes(row(activity="")))

    assert not plan.ok
    assert messages_by_line(plan)[2] == "Die Tätigkeit fehlt."
    assert plan.entries == []


def test_import_is_logged(member, group, activity, superuser):
    csv_import.run(csv_bytes(row()), actor=superuser)

    entry = TimeEntry.objects.get()
    rows = AuditLog.objects.filter(action=AuditLog.Action.ENTRIES_IMPORTED)
    assert rows.count() == 2
    per_entry = rows.get(target_id=entry.pk)
    assert per_entry.target_type == "TimeEntry"
    assert per_entry.actor == superuser
    assert per_entry.subject == member
    assert per_entry.group == group
    assert per_entry.changes["nachher"]["activity"] == "Montage"
    summary = rows.get(target_id__isnull=True)
    assert summary.changes["Zeiten"] == 1
    assert "CSV-Import" in summary.note


def test_large_file_stays_at_a_handful_of_queries(
    member, group, activity, django_assert_max_num_queries
):
    rows = [row(start=f"{hour:02d}:00", end=f"{hour:02d}:30") for hour in range(6, 20)]
    data = csv_bytes(*rows)

    # Sechs Abfragen zum Prüfen, danach gebündeltes Schreiben: keine Abfrage
    # je Zeile. Der Wert darf nicht mit der Zeilenzahl wachsen.
    with django_assert_max_num_queries(15):
        result = csv_import.run(data)

    assert result.count == 14


# --- fehlerhafte Zeilen -----------------------------------------------------


def messages_by_line(plan) -> dict[int, str]:
    return {error.line: error.message for error in plan.errors}


def test_unknown_user_group_and_activity(member, group, activity):
    data = csv_bytes(
        row(email="niemand@example.com"),
        row(group="Kantine"),
        row(activity="Schweissen"),
    )

    plan = csv_import.prepare(data)

    assert not plan.ok
    problems = messages_by_line(plan)
    assert "kein Konto" in problems[2]
    assert "Kantine" in problems[3]
    assert "Schweissen" in problems[4]


def test_non_member_and_foreign_activity(member, group, other_group, activity):
    GroupMembership.objects.create(user=member, group=other_group)
    foreign = Activity.objects.create(group=other_group, name="Ablage")
    data = csv_bytes(
        row(group="Buero", activity="Ablage", start="08:00", end="09:00"),
        row(group="Werkstatt", activity="Ablage", start="10:00", end="11:00"),
    )
    # Die zweite Zeile nennt eine Tätigkeit der anderen Gruppe.
    assert foreign.group == other_group

    plan = csv_import.prepare(data)

    problems = messages_by_line(plan)
    assert 2 not in problems
    assert "Ablage" in problems[3]


def test_membership_is_required(make_user, group, activity):
    outsider = make_user("extern@example.com", first_name="Ex", last_name="Tern")
    data = csv_bytes(row(email=outsider.email))

    plan = csv_import.prepare(data)

    assert "kein Mitglied" in messages_by_line(plan)[2]


def test_end_must_be_after_start(member, group, activity):
    data = csv_bytes(row(start="03.02.2025 10:00", end="03.02.2025 08:00", day=""))

    plan = csv_import.prepare(data)

    assert "Ende muss nach dem Beginn" in messages_by_line(plan)[2]


def test_broken_values_are_named_with_their_line(member, group, activity):
    data = csv_bytes(
        row(start="Viertel nach acht"),
        row(start="09:00", end="10:00", breaks="übertrieben"),
        row(start="11:00", end="12:00", breaks="120"),
    )

    plan = csv_import.prepare(data)

    problems = messages_by_line(plan)
    assert "kein gültiger Beginn" in problems[2]
    assert "Pausenangabe" in problems[3]
    assert "so lang wie die Arbeitszeit" in problems[4]


def test_overlap_with_existing_entry(member, group, activity):
    TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=at(DAY, 7), end=at(DAY, 9)
    )
    data = csv_bytes(row())

    plan = csv_import.prepare(data)

    assert "bereits erfassten Zeit" in messages_by_line(plan)[2]


def test_overlap_with_an_open_entry(member, group, activity):
    TimeEntry.objects.create(user=member, group=group, activity=activity, start=at(DAY, 7))
    data = csv_bytes(row())

    plan = csv_import.prepare(data)

    assert "bereits erfassten Zeit" in messages_by_line(plan)[2]


def test_overlap_inside_the_file(member, group, activity):
    data = csv_bytes(
        row(start="08:00", end="12:00"),
        row(start="11:00", end="13:00"),
        row(start="13:00", end="14:00"),
    )

    plan = csv_import.prepare(data)

    problems = messages_by_line(plan)
    assert problems.keys() == {3}
    assert "Zeile 2 derselben Datei" in problems[3]


def test_closed_period_blocks_the_row(member, group, activity, superuser):
    PeriodLock.objects.create(
        group=group,
        period_start=date(2025, 2, 1),
        period_end=date(2025, 2, 28),
        closed_by=superuser,
    )
    data = csv_bytes(row())

    plan = csv_import.prepare(data)

    assert "abgeschlossen" in messages_by_line(plan)[2]


def test_nothing_is_written_when_one_row_fails(member, group, activity):
    data = csv_bytes(row(start="08:00", end="09:00"), row(group="Kantine"))

    with pytest.raises(csv_import.CsvImportError) as excinfo:
        csv_import.run(data)

    assert TimeEntry.objects.count() == 0
    assert BreakEntry.objects.count() == 0
    assert AuditLog.objects.count() == 0
    assert len(excinfo.value.plan.errors) == 1


def test_state_is_checked_again_when_writing(member, group, activity):
    """Zwischen Vorschau und Übernahme kann eine Zeit dazukommen."""
    data = csv_bytes(row())
    assert csv_import.prepare(data).ok

    TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=at(DAY, 7), end=at(DAY, 9)
    )

    with pytest.raises(csv_import.CsvImportError):
        csv_import.run(data)
    assert TimeEntry.objects.filter(source=TimeEntry.Source.IMPORT).count() == 0


# --- Dateiformat ------------------------------------------------------------


def test_missing_columns_are_refused(db):
    with pytest.raises(csv_import.CsvImportError, match="fehlen diese Spalten"):
        csv_import.prepare(csv_bytes("a;b", header="E-Mail;Gruppe"))


def test_a_file_without_a_person_column_is_refused(db):
    with pytest.raises(csv_import.CsvImportError, match="Personalnummer"):
        csv_import.prepare(csv_bytes("Werkstatt;08:00;09:00", header="Gruppe;Beginn;Ende"))


def test_empty_file_is_refused(db):
    with pytest.raises(csv_import.CsvImportError, match="leer"):
        csv_import.prepare(b"")


def test_rows_without_a_data_line_are_refused(db):
    with pytest.raises(csv_import.CsvImportError, match="keine Zeilen"):
        csv_import.prepare(csv_bytes())


def test_oversized_upload_is_refused(db):
    with pytest.raises(csv_import.CsvImportError, match="größer als"):
        csv_import.decode(b"x" * (csv_import.MAX_UPLOAD_BYTES + 1))


def test_sample_file_is_german_and_protected_against_formulas():
    data = csv_import.sample_csv()

    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    head, *rest = text.splitlines()
    assert head.split(";")[:3] == ["Personalnummer", "E-Mail", "Gruppe"]
    assert "Tätigkeit" in head
    assert len(rest) == 2


def test_sample_file_survives_a_round_trip(member, group, activity, make_user):
    """Die Beispieldatei muss sich mit passenden Stammdaten auch lesen lassen."""
    Activity.objects.create(group=group, name="Lackieren")
    member.email = "mia.mustermann@example.com"
    member.personnel_number = "1001"
    member.save(update_fields=["email", "personnel_number"])
    second = make_user("max.beispiel@example.com", first_name="Max", last_name="Beispiel")
    second.personnel_number = "1002"
    second.save(update_fields=["personnel_number"])
    GroupMembership.objects.create(user=second, group=group)

    plan = csv_import.prepare(csv_import.sample_csv())

    assert plan.errors == []
    assert len(plan.entries) == 2


def test_sample_cells_are_protected_against_formulas(monkeypatch):
    """Dieselbe Absicherung wie im Export (Issue 15), auch für die Vorlage."""
    formula = "=HYPERLINK(A1)"
    monkeypatch.setattr(
        csv_import, "SAMPLE_ROWS", (("1001", formula, "Werkstatt", "", "", "", "", "", ""),)
    )

    text = csv_import.sample_csv().decode("utf-8-sig")

    assert f";'{formula};" in text
    assert f";{formula};" not in text


# --- Management-Kommando ----------------------------------------------------


def test_command_checks_without_writing(tmp_path, member, group, activity):
    path = tmp_path / "zeiten.csv"
    path.write_bytes(csv_bytes(row()))
    out = StringIO()

    call_command("import_time_entries", str(path), stdout=out)

    assert TimeEntry.objects.count() == 0
    assert "1 Zeiten prüfbar" in out.getvalue()


def test_command_writes_with_the_switch(tmp_path, member, group, activity, superuser):
    path = tmp_path / "zeiten.csv"
    path.write_bytes(csv_bytes(row()))
    out = StringIO()

    call_command(
        "import_time_entries",
        str(path),
        "--uebernehmen",
        "--akteur",
        superuser.email,
        stdout=out,
    )

    entry = TimeEntry.objects.get()
    assert entry.source == TimeEntry.Source.IMPORT
    assert AuditLog.objects.filter(
        action=AuditLog.Action.ENTRIES_IMPORTED, actor=superuser
    ).exists()


def test_command_refuses_a_broken_file(tmp_path, member, group, activity):
    path = tmp_path / "zeiten.csv"
    path.write_bytes(csv_bytes(row(group="Kantine")))

    with pytest.raises(CommandError):
        call_command("import_time_entries", str(path), "--uebernehmen")
    assert TimeEntry.objects.count() == 0


def test_command_refuses_a_non_admin_actor(tmp_path, member, group, activity):
    path = tmp_path / "zeiten.csv"
    path.write_bytes(csv_bytes(row()))

    with pytest.raises(CommandError, match="kein System-Admin"):
        call_command("import_time_entries", str(path), "--akteur", member.email)
