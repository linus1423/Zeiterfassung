"""Kleine Helfer rund um Zeitraeume."""

from datetime import date, datetime, time, timedelta

from django.utils import timezone


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Start und Ende eines Tages in der Anzeigezeitzone, als aware datetimes."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), tz)
    return start, end
