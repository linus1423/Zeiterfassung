"""Abrechnungszeitraeume einer Gruppe.

Eine Gruppe rechnet entweder nach Kalendermonat ab oder nach einem eigenen
Zyklus, der am X. eines Monats beginnt (Issue 8). Alle Stellen, die "Monat"
meinen, fragen hier nach dem Zeitraum, damit Auswertung, Abschluss und
Anzeige dieselbe Rechnung benutzen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Ein Zyklus darf hoechstens am 28. beginnen, damit jeder Monat diesen Tag hat.
MAX_MONTH_START_DAY = 28

MONTH_NAMES = (
    "Januar",
    "Februar",
    "Maerz",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

WEEKDAY_NAMES = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)


def normalize_start_day(value) -> int:
    """Begrenzt den Zyklusbeginn auf 1 bis 28, auch bei kaputten Altdaten."""
    try:
        day = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(day, MAX_MONTH_START_DAY))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def shift_months(day: date, delta: int) -> date:
    """Verschiebt ein Datum um ganze Monate, ohne Zusatzbibliothek.

    Gibt es den Tag im Zielmonat nicht (31. Maerz minus einen Monat), wird auf
    den letzten Tag des Zielmonats gekuerzt.
    """
    year, month = _shift_month(day.year, day.month, delta)
    next_year, next_month = _shift_month(year, month, 1)
    last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


@dataclass(frozen=True)
class Period:
    """Ein Abrechnungszeitraum, Ende einschliesslich."""

    start: date
    end: date
    start_day: int = 1

    @property
    def is_calendar_month(self) -> bool:
        return self.start_day == 1

    @property
    def key(self) -> str:
        """Stabiler Schluessel fuer Formulare und Verdichtung."""
        return self.start.isoformat()

    @property
    def label(self) -> str:
        if self.is_calendar_month:
            return f"{MONTH_NAMES[self.start.month - 1]} {self.start.year}"
        return f"{self.start:%d.%m.%Y} bis {self.end:%d.%m.%Y}"

    @property
    def month_label(self) -> str:
        """Der Monat, dem der Zeitraum zugeordnet wird: der seines Beginns."""
        return MONTH_NAMES[self.start.month - 1]

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def next(self) -> Period:
        return period_for(self.end + timedelta(days=1), self.start_day)

    def previous(self) -> Period:
        return period_for(self.start - timedelta(days=1), self.start_day)


def period_for(day: date, start_day: int = 1) -> Period:
    """Der Zeitraum, in dem dieser Tag liegt."""
    start_day = normalize_start_day(start_day)
    if day.day >= start_day:
        start = date(day.year, day.month, start_day)
    else:
        year, month = _shift_month(day.year, day.month, -1)
        start = date(year, month, start_day)
    year, month = _shift_month(start.year, start.month, 1)
    end = date(year, month, start_day) - timedelta(days=1)
    return Period(start=start, end=end, start_day=start_day)


def period_from_start(start: date, start_day: int = 1) -> Period:
    """Der Zeitraum, der an diesem Tag beginnt."""
    return period_for(start, start_day)


def group_period(group, day: date | None = None) -> Period:
    """Der Zeitraum einer Gruppe, in dem der Tag liegt (heute, wenn leer)."""
    if day is None:
        from django.utils import timezone

        day = timezone.localdate()
    return period_for(day, getattr(group, "month_start_day", 1))


def recent_periods(group, count: int = 12, today: date | None = None) -> list[Period]:
    """Die letzten Zeitraeume einer Gruppe, der aktuelle zuerst."""
    period = group_period(group, today)
    periods = [period]
    for _ in range(max(0, count - 1)):
        period = period.previous()
        periods.append(period)
    return periods
