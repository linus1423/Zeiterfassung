"""Kleine Helfer rund um Zeiträume."""

from datetime import date, datetime, time, timedelta

from django.utils import timezone


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Start und Ende eines Tages in der Anzeigezeitzone, als aware datetimes."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), tz)
    return start, end


def local_day_range(start, end=None) -> tuple[date, date] | None:
    """Die Ortstage, die ein Zeitraum berührt, von seinem ersten bis zu seinem letzten.

    Ein Eintrag kann über Mitternacht laufen und dann mehrere Tage berühren.
    Wer nur Beginn und Ende prüft, übersieht alles dazwischen.
    """
    first = timezone.localtime(start).date() if start is not None else None
    last = timezone.localtime(end).date() if end is not None else None
    if first is None and last is None:
        return None
    first = first or last
    last = last or first
    return (first, last) if first <= last else (last, first)
