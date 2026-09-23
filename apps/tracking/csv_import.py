"""Zeiten aus einer CSV-Datei übernehmen (Issue 54).

Der Import gehört in apps.tracking, weil hier die Zeiteinträge selbst liegen
und weil er genau dieselben Regeln anwenden muss wie jede andere Stelle, die
Zeiten schreibt (apps/tracking/entries.py, apps/tracking/entry_editing.py):
keine Überschneidungen, Tätigkeit aus der richtigen Gruppe, kein
abgeschlossener Zeitraum. In apps.reporting gehört er nicht, dort wird
ausgewertet und ausgegeben, aber nie geschrieben.

Der Ablauf ist immer zweistufig: erst wird die Datei geprüft und das Ergebnis
gezeigt, dann erst geschrieben. Geschrieben wird alles oder nichts; ein
einziger Fehler lässt die Datenbank unverändert.

Auf die Größe geachtet: ein Jahresimport hat viele tausend Zeilen. Nutzer,
Gruppen, Tätigkeiten, Mitgliedschaften, Abschlüsse und die vorhandenen Zeiten
der betroffenen Personen werden je genau einmal geladen, danach wird nur noch
im Speicher verglichen. Geschrieben wird gebündelt.
"""

from __future__ import annotations

import bisect
import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups.models import Activity, Group, GroupMembership, PeriodLock

from .entries import overlap_guard
from .models import BreakEntry, EntryImport, TimeEntry
from .utils import local_day_range

# Die Datei kommt von außen. Beides begrenzt, was ein einzelner Aufruf an
# Speicher und Rechenzeit kosten kann.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_NOTE_LENGTH = 500

# So viele Fehler werden gesammelt; danach lohnt das Weiterlesen nicht mehr,
# die Datei ist ohnehin zu berichtigen.
MAX_ERRORS = 200

# In Blöcken schreiben, damit ein Jahresimport nicht zu einer Abfrage je Zeile
# wird und die einzelne Anweisung nicht beliebig lang gerät.
BATCH_SIZE = 500

# Deutsches CSV: Semikolon und UTF-8 mit BOM, passend zum Export in
# apps/reporting/services.py. Erkannt werden zusätzlich Komma und Tabulator,
# damit eine Datei aus einem Altsystem nicht am Trennzeichen scheitert.
DEFAULT_DELIMITER = ";"
DELIMITERS = (";", ",", "\t")

SAMPLE_FILENAME = "zeiten-vorlage.csv"

# Die Zeichensätze, in denen solche Dateien üblicherweise ankommen: UTF-8 mit
# und ohne BOM zuerst, danach Windows-1252. Letzteres liest jedes Byte und
# bricht deshalb nie ab, steht also am Ende.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")

HEADER_ALIASES = {
    "personalnummer": "personnel_number",
    "personalnr": "personnel_number",
    "persnr": "personnel_number",
    "pnr": "personnel_number",
    "email": "email",
    "emailadresse": "email",
    "mail": "email",
    "gruppe": "group",
    "abteilung": "group",
    "tatigkeit": "activity",
    "aufgabe": "activity",
    "datum": "date",
    "tag": "date",
    "beginn": "start",
    "start": "start",
    "von": "start",
    "beginnzeit": "start",
    "ende": "end",
    "bis": "end",
    "endzeit": "end",
    "pausen": "breaks",
    "pause": "breaks",
    "pausenzeit": "breaks",
    "pausenminuten": "breaks",
    "notiz": "note",
    "bemerkung": "note",
    "kommentar": "note",
}

FIELD_LABELS = {
    "personnel_number": "Personalnummer",
    "email": "E-Mail",
    "group": "Gruppe",
    "activity": "Tätigkeit",
    "date": "Datum",
    "start": "Beginn",
    "end": "Ende",
    "breaks": "Pausen",
    "note": "Notiz",
}

# Spalten der Beispieldatei, in dieser Reihenfolge.
SAMPLE_COLUMNS = (
    "personnel_number",
    "email",
    "group",
    "activity",
    "date",
    "start",
    "end",
    "breaks",
    "note",
)

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y")
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%H.%M")

# Ein laufender Eintrag hat kein Ende und belegt damit alles nach seinem Beginn.
FOREVER = datetime.max.replace(tzinfo=UTC)

User = get_user_model()


class CsvImportError(Exception):
    """Die Datei kann so nicht importiert werden.

    Trägt den geprüften Plan mit, damit die Oberfläche die Fehler je Zeile
    zeigen kann und nicht nur einen Satz.
    """

    def __init__(self, message: str, plan: ImportPlan | None = None):
        super().__init__(message)
        self.plan = plan


# --- Datenklassen -----------------------------------------------------------


@dataclass(frozen=True)
class RowError:
    """Ein Fehler in einer Zeile, mit Zeilennummer und Begründung."""

    line: int
    message: str


@dataclass(frozen=True)
class RawRow:
    """Eine Datenzeile, noch rein textlich."""

    line: int
    values: dict[str, str]


@dataclass
class PlannedEntry:
    """Eine geprüfte Zeile, bereit zum Schreiben."""

    line: int
    user: object
    group: Group
    activity: Activity
    start: datetime
    end: datetime
    breaks: list[tuple[datetime, datetime]]
    note: str

    @property
    def gross(self) -> timedelta:
        # Über UTC, damit die Umstellung auf Sommer- oder Winterzeit nicht
        # unterschlagen wird (wie in TimeEntry.elapsed).
        return self.end.astimezone(UTC) - self.start.astimezone(UTC)

    @property
    def break_total(self) -> timedelta:
        return sum(
            ((stop.astimezone(UTC) - begin.astimezone(UTC)) for begin, stop in self.breaks),
            timedelta(),
        )

    @property
    def work(self) -> timedelta:
        value = self.gross - self.break_total
        return value if value > timedelta() else timedelta()


@dataclass
class ImportPlan:
    """Das Ergebnis der Prüfung einer Datei."""

    row_count: int = 0
    entries: list[PlannedEntry] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    users: list[object] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    # Die Gesamtzahl der Fehler; errors trägt höchstens MAX_ERRORS davon.
    error_count: int = 0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    @property
    def first_day(self) -> date | None:
        if not self.entries:
            return None
        return min(timezone.localtime(item.start).date() for item in self.entries)

    @property
    def last_day(self) -> date | None:
        if not self.entries:
            return None
        # Eine Minute vor dem Ende: eine Schicht, die um Punkt Mitternacht
        # endet, gehört noch zum Vortag (wie TimeEntry.spans_days).
        return max(
            timezone.localtime(item.end - timedelta(microseconds=1)).date() for item in self.entries
        )

    @property
    def total_work(self) -> timedelta:
        """Die Arbeitszeit des ganzen Laufs.

        Bewusst ohne apps/tracking/daysplit.py: aufgeteilt wird nur, wo je Tag
        gezählt wird. Die Summe über den gesamten Lauf ist dieselbe, ob eine
        Schicht über Mitternacht läuft oder nicht.
        """
        return sum((item.work for item in self.entries), timedelta())

    @property
    def total_hours(self) -> float:
        return round(self.total_work.total_seconds() / 3600, 2)

    def summary(self) -> dict:
        """Die Zusammenfassung eines Laufs, für Anzeige und Protokoll."""
        return {
            "Zeilen": self.row_count,
            "Zeiten": len(self.entries),
            "Nutzer": len(self.users),
            "Gruppen": len(self.groups),
            "Fehler": self.error_count,
            "erster Tag": self.first_day.isoformat() if self.first_day else None,
            "letzter Tag": self.last_day.isoformat() if self.last_day else None,
            "Stunden": self.total_hours,
        }

    def note(self) -> str:
        """Ein Satz für das Protokoll."""
        zeitraum = ""
        if self.first_day and self.last_day:
            zeitraum = f", {self.first_day:%d.%m.%Y} bis {self.last_day:%d.%m.%Y}"
        return (
            f"CSV-Import: {len(self.entries)} Zeiten für {len(self.users)} Personen "
            f"in {len(self.groups)} Gruppen{zeitraum}, {self.total_hours} Stunden."
        )


@dataclass(frozen=True)
class ImportResult:
    """Was ein Lauf tatsächlich geschrieben hat."""

    count: int
    plan: ImportPlan


# --- Zeichensatz und Kopfzeile ---------------------------------------------


def decode(data: bytes) -> str:
    """Macht aus den hochgeladenen Bytes Text, so robust wie vertretbar."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise CsvImportError(
            f"Die Datei ist größer als {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
            "und wird deshalb nicht gelesen."
        )
    if not data.strip():
        raise CsvImportError("Die Datei ist leer.")

    for encoding in ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        # Ein Nullbyte bringt das CSV-Modul zum Abbruch und hat in einer
        # Zeitliste ohnehin nichts zu suchen.
        return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")

    raise CsvImportError(
        "Der Zeichensatz der Datei konnte nicht erkannt werden. "
        "Bitte als UTF-8 oder Windows-1252 speichern."
    )


def _key(value: str) -> str:
    """Normalisiert eine Beschriftung: klein, ohne Umlaute und Sonderzeichen."""
    lowered = value.strip().lstrip("﻿").lower()
    for umlaut, plain in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        lowered = lowered.replace(umlaut, plain)
    return "".join(char for char in lowered if char.isalnum())


def sniff_delimiter(line: str) -> str:
    """Das Trennzeichen der Kopfzeile. Im Zweifel das deutsche Semikolon."""
    best, count = DEFAULT_DELIMITER, line.count(DEFAULT_DELIMITER)
    for candidate in DELIMITERS:
        found = line.count(candidate)
        if found > count:
            best, count = candidate, found
    return best


def _map_header(header: list[str]) -> dict[str, int]:
    """Ordnet die gefundenen Spalten den bekannten Feldern zu."""
    columns: dict[str, int] = {}
    for index, label in enumerate(header):
        name = HEADER_ALIASES.get(_key(label))
        if name is None:
            continue
        if name in columns:
            raise CsvImportError(
                f"Die Spalte „{FIELD_LABELS[name]}“ kommt in der Kopfzeile mehrfach vor."
            )
        columns[name] = index

    missing = [FIELD_LABELS[name] for name in ("group", "start", "end") if name not in columns]
    if missing:
        raise CsvImportError("In der Kopfzeile fehlen diese Spalten: " + ", ".join(missing) + ".")
    if "personnel_number" not in columns and "email" not in columns:
        raise CsvImportError(
            "Die Kopfzeile braucht eine Spalte „Personalnummer“ oder „E-Mail“, "
            "damit die Zeiten einer Person zugeordnet werden können."
        )
    return columns


def parse_rows(text: str) -> list[RawRow]:
    """Liest die Datei in Zeilen mit benannten Feldern."""
    stream = io.StringIO(text)
    delimiter = sniff_delimiter(text.split("\n", 1)[0])
    reader = csv.reader(stream, delimiter=delimiter)

    try:
        header = next(reader, None)
    except csv.Error as exc:
        raise CsvImportError(f"Die Kopfzeile konnte nicht gelesen werden: {exc}") from exc
    if not header:
        raise CsvImportError("Die Datei hat keine Kopfzeile.")

    columns = _map_header(header)
    rows: list[RawRow] = []
    try:
        for values in reader:
            if not any(value.strip() for value in values):
                continue
            if len(rows) >= MAX_ROWS:
                raise CsvImportError(
                    f"Die Datei hat mehr als {MAX_ROWS} Datenzeilen. "
                    "Bitte in kleinere Dateien aufteilen."
                )
            rows.append(
                RawRow(
                    line=reader.line_num,
                    values={
                        name: (values[index].strip() if index < len(values) else "")
                        for name, index in columns.items()
                    },
                )
            )
    except csv.Error as exc:
        raise CsvImportError(f"Die Datei ist kein gültiges CSV: {exc}") from exc

    if not rows:
        raise CsvImportError("Die Datei enthält außer der Kopfzeile keine Zeilen.")
    return rows


# --- Datum, Uhrzeit und Pausen ---------------------------------------------


def _aware(value: datetime) -> datetime:
    return timezone.make_aware(value, timezone.get_current_timezone())


def _parse_date(value: str) -> date | None:
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _parse_time(value: str) -> time | None:
    for pattern in TIME_FORMATS:
        try:
            return datetime.strptime(value, pattern).time()
        except ValueError:
            continue
    return None


def _split_moment(value: str) -> tuple[date | None, time | None]:
    """Zerlegt „01.02.2025 07:30“, „2025-02-01T07:30“ oder „07:30“."""
    parts = value.replace("T", " ").split()
    if len(parts) >= 2:
        return _parse_date(parts[0]), _parse_time(parts[1])
    if len(parts) == 1:
        moment = _parse_time(parts[0])
        if moment is not None:
            return None, moment
        return _parse_date(parts[0]), None
    return None, None


def _place(moment: time, not_before: datetime) -> datetime:
    """Der erste Zeitpunkt mit dieser Ortszeit, der nicht vor not_before liegt."""
    day = timezone.localtime(not_before).date()
    candidate = _aware(datetime.combine(day, moment))
    if candidate < not_before:
        candidate = _aware(datetime.combine(day + timedelta(days=1), moment))
    return candidate


def _parse_duration(text: str) -> int:
    """Eine Pausendauer als Minuten, geschrieben als „30“ oder als „0:30“."""
    try:
        if ":" in text:
            hours, _, minutes = text.partition(":")
            return int(hours) * 60 + int(minutes)
        # „30,5“ aus einer Tabellenkalkulation: die angefangene Minute zählt nicht.
        return int(text.replace(",", ".").split(".")[0])
    except ValueError as exc:
        raise ValueError(
            "Die Pausenangabe ist weder eine Dauer (30 oder 0:30) noch ein "
            "Zeitraum der Form 11:30-12:00."
        ) from exc


def _parse_breaks(value: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Die Pausen einer Zeile.

    Zwei Schreibweisen sind erlaubt, weil Altsysteme beides liefern:
    eine Dauer („30“ oder „0:30“) wird mittig in die Arbeitszeit gelegt, denn
    die Datei sagt dann nicht, wann pausiert wurde; „11:30-12:00“ sind
    ausdrückliche Pausenzeiten, mehrere davon durch Komma getrennt.
    """
    text = value.strip()
    if not text:
        return []

    if "-" not in text and "–" not in text:
        minutes = _parse_duration(text)
        if minutes < 0:
            raise ValueError("Die Pausendauer darf nicht negativ sein.")
        if minutes == 0:
            return []
        pause = timedelta(minutes=minutes)
        gross = end.astimezone(UTC) - start.astimezone(UTC)
        if pause >= gross:
            raise ValueError("Die Pause ist so lang wie die Arbeitszeit oder länger.")
        # Mittig gelegt und auf volle Minuten gerundet: die Datei sagt nur,
        # wie lange pausiert wurde, nicht wann.
        offset = (gross - pause) / 2
        pause_start = start + timedelta(minutes=round(offset.total_seconds() / 60))
        pause_start = min(max(pause_start, start), end - pause)
        return [(pause_start, pause_start + pause)]

    pauses: list[tuple[datetime, datetime]] = []
    cursor = start
    for part in text.replace("–", "-").split(","):
        piece = part.strip()
        if not piece:
            continue
        halves = piece.split("-")
        if len(halves) != 2:
            raise ValueError(f"„{piece}“ ist kein Pausenzeitraum der Form 11:30-12:00.")
        first, second = _parse_time(halves[0].strip()), _parse_time(halves[1].strip())
        if first is None or second is None:
            raise ValueError(f"„{piece}“ enthält keine gültige Uhrzeit.")
        begin = _place(first, cursor)
        stop = _place(second, begin + timedelta(microseconds=1))
        if begin < start or stop > end:
            raise ValueError(
                f"Die Pause {piece} liegt nicht innerhalb der Arbeitszeit. "
                "Mehrere Pausen bitte in zeitlicher Reihenfolge angeben."
            )
        pauses.append((begin, stop))
        cursor = stop
    return pauses


# --- Nachschlagen ohne Abfrage je Zeile -------------------------------------


@dataclass
class UserIndex:
    """Die Nutzer, die in der Datei vorkommen, einmal geladen."""

    by_email: dict[str, object] = field(default_factory=dict)
    by_number: dict[str, object] = field(default_factory=dict)
    ambiguous_numbers: set[str] = field(default_factory=set)


def _identifiers(rows: list[RawRow]) -> tuple[set[str], set[str]]:
    emails = {row.values.get("email", "").strip().lower() for row in rows}
    numbers = {row.values.get("personnel_number", "").strip() for row in rows}
    return {value for value in emails if value}, {value for value in numbers if value}


def load_users(rows: list[RawRow], *, lock: bool = False) -> UserIndex:
    """Lädt genau die Nutzer, die in der Datei stehen, in einer Abfrage.

    Mit lock=True werden ihre Zeilen zugleich für die Dauer der Transaktion
    gesperrt. Ohne diese Sperre könnten zwischen Prüfung und Schreiben andere
    Zeiten derselben Person entstehen (wie in entries.lock_user).
    """
    emails, numbers = _identifiers(rows)
    if not emails and not numbers:
        return UserIndex()

    condition = Q(pk__in=[])
    if emails:
        condition |= Q(email__in=list(emails))
    if numbers:
        condition |= Q(personnel_number__in=list(numbers))

    queryset = User.objects.filter(condition)
    if lock:
        # Nach Schlüssel geordnet sperren, damit zwei gleichzeitige Importe
        # sich nicht gegenseitig blockieren.
        queryset = queryset.select_for_update().order_by("pk")

    index = UserIndex()
    for person in queryset:
        if person.email:
            index.by_email[person.email.strip().lower()] = person
        number = (person.personnel_number or "").strip()
        if not number:
            continue
        if number in index.by_number:
            index.ambiguous_numbers.add(number)
        else:
            index.by_number[number] = person
    for number in index.ambiguous_numbers:
        index.by_number.pop(number, None)
    return index


class Occupancy:
    """Belegte Zeiten je Nutzer, im Speicher und ohne Abfrage je Zeile.

    Hier stehen sowohl die bereits erfassten Zeiten als auch die Zeilen der
    Datei. Damit fällt dieselbe Prüfung auf beides an: eine Zeile darf weder
    mit dem Bestand noch mit einer anderen Zeile der Datei kollidieren.
    """

    def __init__(self):
        self._starts: dict[int, list[datetime]] = {}
        self._items: dict[int, list[tuple[datetime, datetime, str]]] = {}

    def add(self, user_id: int, start: datetime, end: datetime | None, label: str) -> None:
        starts = self._starts.setdefault(user_id, [])
        items = self._items.setdefault(user_id, [])
        index = bisect.bisect_right(starts, start)
        starts.insert(index, start)
        items.insert(index, (start, end or FOREVER, label))

    def clash(self, user_id: int, start: datetime, end: datetime) -> str | None:
        """Die Beschriftung der belegten Zeit, mit der sich [start, end) überschneidet."""
        starts = self._starts.get(user_id)
        if not starts:
            return None
        items = self._items[user_id]
        index = bisect.bisect_right(starts, start)
        if index > 0 and items[index - 1][1] > start:
            return items[index - 1][2]
        if index < len(items) and items[index][0] < end:
            return items[index][2]
        return None


def _existing_label(start: datetime) -> str:
    local = timezone.localtime(start)
    return f"einer bereits erfassten Zeit vom {local:%d.%m.%Y} ab {local:%H:%M} Uhr"


def _load_occupancy(user_ids: set[int], first: datetime, last: datetime) -> Occupancy:
    """Die vorhandenen Zeiten der betroffenen Personen, in einer Abfrage."""
    occupancy = Occupancy()
    if not user_ids:
        return occupancy

    queryset = (
        TimeEntry.objects.filter(user_id__in=list(user_ids), start__lt=last)
        .filter(Q(end__isnull=True) | Q(end__gt=first))
        .values_list("user_id", "start", "end")
    )
    for user_id, start, end in queryset:
        occupancy.add(user_id, start, end, _existing_label(start))
    return occupancy


class NameIndex:
    """Nachschlagen über den genauen Namen, sonst über die entschärfte Form.

    Gruppen- und Tätigkeitsnamen kommen aus einem Altsystem und stimmen selten
    bis aufs Zeichen. Deshalb wird auch ohne Umlaute, Groß- und Kleinschreibung
    gesucht — aber nur, solange das eindeutig bleibt: „Büro“ und „Buero“ wären
    sonst nicht zu unterscheiden, und eine Zeit landete in der falschen Gruppe.
    """

    def __init__(self):
        self._exact: dict[str, object] = {}
        self._loose: dict[str, object] = {}
        self._ambiguous: set[str] = set()

    def add(self, name: str, value) -> None:
        if not name:
            return
        self._exact.setdefault(name.strip(), value)
        key = _key(name)
        if not key:
            return
        known = self._loose.get(key)
        if known is None:
            self._loose[key] = value
        elif known is not value:
            self._ambiguous.add(key)

    def find(self, name: str) -> tuple[object | None, bool]:
        """Der Treffer und, falls es keinen gibt, ob der Name mehrdeutig war."""
        value = self._exact.get(name.strip())
        if value is not None:
            return value, False
        key = _key(name)
        if key in self._ambiguous:
            return None, True
        return self._loose.get(key), False


class LockIndex:
    """Alle Abschlüsse der betroffenen Gruppen, einmal geladen."""

    def __init__(self, group_ids: set[int]):
        self._by_group: dict[int, list[tuple[date, date, str]]] = {}
        if not group_ids:
            return
        for lock in PeriodLock.objects.filter(group_id__in=list(group_ids)):
            self._by_group.setdefault(lock.group_id, []).append(
                (lock.period_start, lock.period_end, lock.period.label)
            )

    def blocking(self, group_id: int, first: date, last: date) -> str | None:
        for start, end, label in self._by_group.get(group_id, ()):
            if start <= last and end >= first:
                return label
        return None


# --- Prüfung ----------------------------------------------------------------


@dataclass
class _Candidate:
    """Eine Zeile nach dem rein textlichen Teil der Prüfung."""

    line: int
    user: object
    group: Group
    activity_name: str
    start: datetime
    end: datetime
    breaks: list[tuple[datetime, datetime]]
    note: str


def _resolve_user(row: RawRow, users: UserIndex) -> tuple[object | None, str | None]:
    """Findet die Person über E-Mail oder Personalnummer."""
    email = row.values.get("email", "").strip().lower()
    number = row.values.get("personnel_number", "").strip()
    if not email and not number:
        return None, "Die Zeile nennt weder eine E-Mail-Adresse noch eine Personalnummer."

    by_email = users.by_email.get(email) if email else None
    if email and by_email is None:
        return None, f"Zu der E-Mail-Adresse „{email}“ gibt es kein Konto."
    if number and number in users.ambiguous_numbers:
        return None, f"Die Personalnummer „{number}“ ist mehreren Konten zugeordnet."
    by_number = users.by_number.get(number) if number else None
    if number and by_number is None:
        return None, f"Zu der Personalnummer „{number}“ gibt es kein Konto."
    if by_email is not None and by_number is not None and by_email.pk != by_number.pk:
        return None, (
            f"E-Mail-Adresse „{email}“ und Personalnummer „{number}“ "
            "gehören zu verschiedenen Konten."
        )

    person = by_email or by_number
    if person is not None and not person.is_active:
        return None, f"Das Konto von {person.full_name} ist deaktiviert."
    return person, None


def _row_times(row: RawRow) -> tuple[datetime, datetime]:
    """Beginn und Ende einer Zeile, auch über Mitternacht hinweg."""
    day_value = row.values.get("date", "").strip()
    row_day = _parse_date(day_value) if day_value else None
    if day_value and row_day is None:
        raise ValueError(f"„{day_value}“ ist kein gültiges Datum (erwartet TT.MM.JJJJ).")

    start_value = row.values.get("start", "").strip()
    end_value = row.values.get("end", "").strip()
    if not start_value or not end_value:
        raise ValueError("Beginn und Ende müssen gefüllt sein.")

    start_day, start_time = _split_moment(start_value)
    end_day, end_time = _split_moment(end_value)
    if start_time is None:
        raise ValueError(f"„{start_value}“ ist kein gültiger Beginn.")
    if end_time is None:
        raise ValueError(f"„{end_value}“ ist kein gültiges Ende.")

    start_day = start_day or row_day
    if start_day is None:
        raise ValueError(
            "Zum Beginn fehlt das Datum. Entweder „TT.MM.JJJJ HH:MM“ schreiben "
            "oder eine Spalte „Datum“ ergänzen."
        )

    start = _aware(datetime.combine(start_day, start_time))
    if end_day is not None:
        end = _aware(datetime.combine(end_day, end_time))
    else:
        # Nur eine Uhrzeit: ein Ende, das nicht nach dem Beginn liegt, gehört
        # zum Folgetag. So kommt die Nachtschicht richtig an (Issue 32).
        end = _place(end_time, start + timedelta(microseconds=1))

    if end <= start:
        raise ValueError("Das Ende muss nach dem Beginn liegen.")
    return start, end


def _candidates(rows: list[RawRow], users: UserIndex) -> tuple[list[_Candidate], list[RowError]]:
    """Der Teil der Prüfung, der ohne weitere Abfragen auskommt."""
    candidates: list[_Candidate] = []
    errors: list[RowError] = []
    groups = NameIndex()
    for group in Group.objects.all():
        groups.add(group.name, group)
        groups.add(group.slug, group)

    for row in rows:
        person, problem = _resolve_user(row, users)
        if problem is not None:
            errors.append(RowError(row.line, problem))
            continue

        name = row.values.get("group", "").strip()
        group, ambiguous = groups.find(name)
        if ambiguous:
            errors.append(
                RowError(row.line, f"Der Gruppenname „{name}“ passt auf mehrere Gruppen.")
            )
            continue
        if group is None:
            errors.append(RowError(row.line, f"Die Gruppe „{name}“ ist nicht bekannt."))
            continue
        if not group.is_active:
            errors.append(RowError(row.line, f"Die Gruppe „{group.name}“ ist nicht aktiv."))
            continue

        try:
            start, end = _row_times(row)
            pauses = _parse_breaks(row.values.get("breaks", ""), start, end)
        except ValueError as exc:
            errors.append(RowError(row.line, str(exc)))
            continue

        note = row.values.get("note", "")
        if len(note) > MAX_NOTE_LENGTH:
            errors.append(
                RowError(row.line, f"Die Notiz ist länger als {MAX_NOTE_LENGTH} Zeichen.")
            )
            continue

        candidates.append(
            _Candidate(
                line=row.line,
                user=person,
                group=group,
                activity_name=row.values.get("activity", "").strip(),
                start=start,
                end=end,
                breaks=pauses,
                note=note,
            )
        )
    return candidates, errors


def build_plan(rows: list[RawRow], *, users: UserIndex | None = None) -> ImportPlan:
    """Prüft alle Zeilen und liefert den Plan samt Fehlern je Zeile."""
    users = users if users is not None else load_users(rows)
    candidates, errors = _candidates(rows, users)

    user_ids = {item.user.pk for item in candidates}
    group_ids = {item.group.pk for item in candidates}

    # Ab hier vier Abfragen für die ganze Datei, nicht je Zeile eine.
    activities: dict[int, NameIndex] = {}
    for activity in Activity.objects.filter(group_id__in=list(group_ids)):
        activities.setdefault(activity.group_id, NameIndex()).add(activity.name, activity)
    memberships = set(
        GroupMembership.objects.filter(
            user_id__in=list(user_ids), group_id__in=list(group_ids), group__is_active=True
        ).values_list("user_id", "group_id")
    )
    locks = LockIndex(group_ids)
    occupancy = (
        _load_occupancy(
            user_ids,
            min(item.start for item in candidates),
            max(item.end for item in candidates),
        )
        if candidates
        else Occupancy()
    )

    entries: list[PlannedEntry] = []
    seen_users: dict[int, object] = {}
    seen_groups: dict[int, Group] = {}
    for item in candidates:
        seen_users.setdefault(item.user.pk, item.user)
        seen_groups.setdefault(item.group.pk, item.group)

        if (item.user.pk, item.group.pk) not in memberships:
            errors.append(
                RowError(
                    item.line,
                    f"{item.user.full_name} ist kein Mitglied der Gruppe {item.group.name}.",
                )
            )
            continue

        # Ohne Tätigkeit entsteht kein Zeiteintrag mehr (Issue 83).
        if not item.activity_name:
            errors.append(RowError(item.line, "Die Tätigkeit fehlt."))
            continue
        index = activities.get(item.group.pk)
        activity, ambiguous = index.find(item.activity_name) if index else (None, False)
        if ambiguous:
            errors.append(
                RowError(
                    item.line,
                    f"Der Name „{item.activity_name}“ passt in der Gruppe "
                    f"{item.group.name} auf mehrere Tätigkeiten.",
                )
            )
            continue
        if activity is None:
            errors.append(
                RowError(
                    item.line,
                    f"Die Tätigkeit „{item.activity_name}“ gibt es in der Gruppe "
                    f"{item.group.name} nicht.",
                )
            )
            continue

        days = local_day_range(item.start, item.end)
        label = locks.blocking(item.group.pk, *days)
        if label is not None:
            errors.append(
                RowError(
                    item.line,
                    f"Der Zeitraum {label} der Gruppe {item.group.name} ist abgeschlossen.",
                )
            )
            continue

        clash = occupancy.clash(item.user.pk, item.start, item.end)
        if clash is not None:
            errors.append(RowError(item.line, f"Die Zeit überschneidet sich mit {clash}."))
            continue

        occupancy.add(item.user.pk, item.start, item.end, f"Zeile {item.line} derselben Datei")
        entries.append(
            PlannedEntry(
                line=item.line,
                user=item.user,
                group=item.group,
                activity=activity,
                start=item.start,
                end=item.end,
                breaks=item.breaks,
                note=item.note,
            )
        )

    errors.sort(key=lambda item: item.line)
    plan = ImportPlan(
        row_count=len(rows),
        entries=entries,
        errors=errors[:MAX_ERRORS],
        error_count=len(errors),
        users=sorted(seen_users.values(), key=lambda person: person.full_name),
        groups=sorted(seen_groups.values(), key=lambda group: group.name),
        truncated=len(errors) > MAX_ERRORS,
    )
    return plan


def prepare(data: bytes) -> ImportPlan:
    """Prüft eine hochgeladene Datei, ohne etwas zu schreiben."""
    return build_plan(parse_rows(decode(data)))


# --- Schreiben --------------------------------------------------------------


def _snapshot(entry: TimeEntry, planned: PlannedEntry) -> dict:
    """Wie entries.snapshot, aber ohne Abfrage je Eintrag."""
    return {
        "start": entry.start.isoformat(),
        "end": entry.end.isoformat(),
        "activity": planned.activity.name if planned.activity else None,
        "note": entry.note,
        "breaks": [
            {"start": begin.isoformat(), "end": stop.isoformat()} for begin, stop in planned.breaks
        ],
    }


def _write(plan: ImportPlan, *, actor) -> int:
    """Schreibt den geprüften Plan gebündelt."""
    entries = [
        TimeEntry(
            user=item.user,
            group=item.group,
            activity=item.activity,
            start=item.start,
            end=item.end,
            note=item.note,
            source=TimeEntry.Source.IMPORT,
        )
        for item in plan.entries
    ]

    with overlap_guard(CsvImportError):
        if connection.features.can_return_rows_from_bulk_insert:
            TimeEntry.objects.bulk_create(entries, batch_size=BATCH_SIZE)
        else:
            # Ohne RETURNING liefert bulk_create keine Schlüssel, die Pausen
            # bräuchten sie aber. Dann eben einzeln.
            for entry in entries:
                entry.save(force_insert=True)

    pauses = [
        BreakEntry(time_entry=entry, start=begin, end=stop)
        for entry, item in zip(entries, plan.entries, strict=True)
        for begin, stop in item.breaks
    ]
    if pauses:
        BreakEntry.objects.bulk_create(pauses, batch_size=BATCH_SIZE)

    actor_id = actor.pk if (actor is not None and actor.is_authenticated) else None
    records = [
        AuditLog(
            actor_id=actor_id,
            action=AuditLog.Action.ENTRIES_IMPORTED,
            target_type="TimeEntry",
            target_id=entry.pk,
            group=item.group,
            subject=item.user,
            changes={"nachher": _snapshot(entry, item)},
            note=f"Aus CSV importiert (Zeile {item.line}).",
        )
        for entry, item in zip(entries, plan.entries, strict=True)
    ]
    records.append(
        AuditLog(
            actor_id=actor_id,
            action=AuditLog.Action.ENTRIES_IMPORTED,
            changes=plan.summary(),
            note=plan.note(),
        )
    )
    AuditLog.objects.bulk_create(records, batch_size=BATCH_SIZE)
    return len(entries)


@transaction.atomic
def run(data: bytes, *, actor=None) -> ImportResult:
    """Prüft und schreibt in einer Transaktion. Bei einem Fehler bleibt alles, wie es war.

    Geprüft wird hier noch einmal vollständig, nicht nur beim Hochladen:
    zwischen Vorschau und Übernahme kann jemand eine Zeit nachgetragen oder
    einen Zeitraum abgeschlossen haben.
    """
    rows = parse_rows(decode(data))
    # Erst sperren, dann prüfen: sonst könnte zwischen Prüfung und Schreiben
    # eine überschneidende Zeit derselben Person entstehen.
    users = load_users(rows, lock=True)
    plan = build_plan(rows, users=users)
    if not plan.ok:
        raise CsvImportError(
            f"Die Datei hat {plan.error_count} fehlerhafte Zeilen. Es wurde nichts importiert.",
            plan,
        )
    count = _write(plan, actor=actor)
    return ImportResult(count=count, plan=plan)


@transaction.atomic
def apply_record(record: EntryImport, *, actor) -> ImportResult:
    """Übernimmt eine vorgemerkte Datei und hält den Lauf im Datensatz fest."""
    if record.status != EntryImport.Status.PREPARED:
        raise CsvImportError("Dieser Import wurde bereits übernommen.")

    result = run(bytes(record.payload), actor=actor)
    record.status = EntryImport.Status.APPLIED
    record.applied_at = timezone.now()
    record.summary = result.plan.summary()
    # Die Datei enthält personenbezogene Zeiten und wird nach der Übernahme
    # nicht mehr gebraucht; die Zusammenfassung bleibt.
    record.payload = b""
    record.save(update_fields=["status", "applied_at", "summary", "payload"])
    return result


def purge_stale(hours: int = 24) -> int:
    """Räumt geprüfte, aber nie übernommene Dateien wieder weg."""
    cutoff = timezone.now() - timedelta(hours=hours)
    deleted, _ = EntryImport.objects.filter(
        status=EntryImport.Status.PREPARED, created_at__lt=cutoff
    ).delete()
    return deleted


# --- Beispieldatei ----------------------------------------------------------

SAMPLE_ROWS = (
    (
        "1001",
        "mia.mustermann@example.com",
        "Werkstatt",
        "Montage",
        "01.02.2025",
        "07:30",
        "16:00",
        "30",
        "Aus dem Altsystem",
    ),
    (
        "1002",
        "max.beispiel@example.com",
        "Werkstatt",
        "Lackieren",
        "01.02.2025",
        "22:00",
        "06:00",
        "01:00-01:30",
        "Nachtschicht",
    ),
)


def sample_csv() -> bytes:
    """Die Beispieldatei mit Kopfzeile, im deutschen Format.

    Dieselbe Absicherung gegen Formel-Einschleusung wie im Export (Issue 15):
    die Datei wird in Excel geöffnet, und was mit = + - @ beginnt, würde dort
    sonst ausgeführt.
    """
    from apps.reporting.services import csv_safe

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=DEFAULT_DELIMITER, lineterminator="\r\n")
    writer.writerow([csv_safe(FIELD_LABELS[name]) for name in SAMPLE_COLUMNS])
    for row in SAMPLE_ROWS:
        writer.writerow([csv_safe(value) for value in row])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
