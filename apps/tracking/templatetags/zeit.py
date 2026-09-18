from datetime import timedelta

from django import template

register = template.Library()


@register.filter
def dauer(value) -> str:
    """Formatiert eine Zeitspanne als hh:mm."""
    if not isinstance(value, timedelta):
        return ""
    minutes = int(value.total_seconds() // 60)
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"


@register.filter
def stunden(value) -> str:
    """Formatiert eine Zeitspanne als Dezimalstunden."""
    if not isinstance(value, timedelta):
        return ""
    return f"{value.total_seconds() / 3600:.2f}".replace(".", ",")
