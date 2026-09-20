"""Zeiteinträge an der Tagesgrenze aufteilen (Issue 32).

Kapitel 4 der Spezifikation: "Für die Tagesauswertung wird er am
Tagesgrenzpunkt anteilig aufgeteilt, in der Datenbank aber nicht zerlegt."
Eine Schicht von 22:00 bis 06:00 gehört also mit zwei Stunden zum ersten und
mit sechs zum zweiten Tag, und die Pausen zählen dort, wo sie liegen.

Aufgeteilt wird über die Ortszeit und nicht über feste 24 Stunden: beim
Wechsel zwischen Sommer- und Winterzeit hat ein Tag 23 oder 25 Stunden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import TimeEntry
from .utils import day_bounds


@dataclass(frozen=True)
class DayPart:
    """Der Anteil eines Zeiteintrags, der auf einen Tag entfällt."""

    entry: TimeEntry
    day: date
    start: datetime
    end: datetime
    breaks: timedelta

    @property
    def gross(self) -> timedelta:
        return self.end - self.start

    @property
    def work(self) -> timedelta:
        value = self.gross - self.breaks
        return value if value > timedelta() else timedelta()

    @property
    def is_partial(self) -> bool:
        """Läuft der Eintrag über diesen Tag hinaus oder in ihn hinein?"""
        return self.start != self.entry.start or self.end != (self.entry.end or self.end)


def _in_utc(value: datetime) -> datetime:
    """Auf UTC drehen, bevor gerechnet wird.

    Python zieht zwei Zeitpunkte mit derselben Zeitzone ohne Rücksicht auf den
    Sommerzeitsprung voneinander ab. Von 00:00 bis 06:00 am Tag der Umstellung
    kämen so sechs Stunden heraus statt fünf.
    """
    return value.astimezone(UTC)


def _overlap(start, end, window_start, window_end) -> timedelta:
    first = max(start, window_start)
    last = min(end, window_end)
    return last - first if last > first else timedelta()


def day_parts(entry: TimeEntry, *, first_day: date | None = None, last_day: date | None = None):
    """Die Tagesanteile eines Eintrags, wahlweise auf einen Zeitraum begrenzt.

    Ein laufender Eintrag zählt bis jetzt, wie überall sonst auch.
    """
    now = timezone.now()
    end = entry.end or now
    if end <= entry.start:
        return []

    pauses = [(pause.start, pause.end or now) for pause in entry.breaks.all()]
    day = timezone.localtime(entry.start).date()
    final_day = timezone.localtime(end).date()

    parts = []
    while day <= final_day:
        if (first_day is None or day >= first_day) and (last_day is None or day <= last_day):
            window_start, window_end = day_bounds(day)
            part_start = max(_in_utc(entry.start), _in_utc(window_start))
            part_end = min(_in_utc(end), _in_utc(window_end))
            if part_end > part_start:
                pause = sum(
                    (_overlap(start, stop, part_start, part_end) for start, stop in pauses),
                    timedelta(),
                )
                parts.append(
                    DayPart(entry=entry, day=day, start=part_start, end=part_end, breaks=pause)
                )
        day += timedelta(days=1)
    return parts


def parts_in_range(entries, first_day: date | None = None, last_day: date | None = None) -> list:
    """Die Tagesanteile mehrerer Einträge, chronologisch."""
    parts = []
    for entry in entries:
        parts.extend(day_parts(entry, first_day=first_day, last_day=last_day))
    parts.sort(key=lambda part: part.start)
    return parts


def entries_in_range(queryset, first_day: date, last_day: date):
    """Einträge, die den Zeitraum berühren.

    Auch die, die davor beginnen und hineinlaufen: sonst fehlte am 1. die
    Stunde, die eine Nachtschicht vom 31. dort gearbeitet hat.
    """
    window_start, _ = day_bounds(first_day)
    _, window_end = day_bounds(last_day)
    return queryset.filter(start__lt=window_end).filter(
        Q(end__isnull=True) | Q(end__gt=window_start)
    )


def work_in_range(entries, first_day: date, last_day: date) -> timedelta:
    """Die Arbeitszeit, die im Zeitraum liegt."""
    return sum((part.work for part in parts_in_range(entries, first_day, last_day)), timedelta())
