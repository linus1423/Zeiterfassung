"""Definition der waehlbaren Exportspalten.

Eine Spalte kennt ihren Schluessel, ihre Beschriftung und ihren Typ. Der Typ
entscheidet, wie der Wert in Excel formatiert wird, damit dort gerechnet
werden kann.
"""

from __future__ import annotations

from dataclasses import dataclass

TEXT = "text"
DATE = "date"
TIME = "time"
NUMBER = "number"
HOURS = "hours"  # Dezimalstunden, z. B. 7.75
HHMM = "hhmm"  # Dauer als hh:mm


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str
    aggregated: bool = True  # auch in verdichteten Zeilen sinnvoll


COLUMNS: tuple[Column, ...] = (
    Column("personnel_number", "Personalnummer", TEXT),
    Column("last_name", "Nachname", TEXT),
    Column("first_name", "Vorname", TEXT),
    Column("full_name", "Name", TEXT),
    Column("email", "E-Mail", TEXT),
    Column("group", "Gruppe", TEXT),
    Column("cost_center", "Kostenstelle", TEXT),
    Column("activity", "Taetigkeit", TEXT),
    Column("date", "Datum", DATE),
    Column("weekday", "Wochentag", TEXT),
    Column("week", "Kalenderwoche", NUMBER),
    Column("month", "Monat", TEXT),
    Column("year", "Jahr", NUMBER),
    Column("start", "Beginn", TIME, aggregated=False),
    Column("end", "Ende", TIME, aggregated=False),
    Column("break_hhmm", "Pausendauer", HHMM),
    Column("hours", "Arbeitszeit (h)", HOURS),
    Column("hhmm", "Arbeitszeit (hh:mm)", HHMM),
    Column("entry_count", "Anzahl Eintraege", NUMBER),
    Column("source", "Erfassungsart", TEXT, aggregated=False),
    Column("incomplete", "Unvollstaendig", TEXT, aggregated=False),
    Column("note", "Notiz", TEXT, aggregated=False),
    Column("updated_at", "Zuletzt geaendert am", TEXT, aggregated=False),
)

COLUMNS_BY_KEY = {column.key: column for column in COLUMNS}

DEFAULT_COLUMNS = [
    "personnel_number",
    "full_name",
    "group",
    "activity",
    "date",
    "start",
    "end",
    "break_hhmm",
    "hours",
]


def resolve(keys: list[str]) -> list[Column]:
    """Bekannte Spalten in der angegebenen Reihenfolge, unbekannte werden verworfen."""
    resolved = [COLUMNS_BY_KEY[key] for key in keys if key in COLUMNS_BY_KEY]
    return resolved or [COLUMNS_BY_KEY[key] for key in DEFAULT_COLUMNS]
