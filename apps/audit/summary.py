"""Protokolleinträge in lesbaren Text übersetzen.

Im Protokoll stehen die Änderungen als Momentaufnahme vorher und nachher
(siehe apps/tracking/entries.snapshot). Für die Anzeige werden daraus Sätze,
damit ein Admin nicht JSON lesen muss.
"""

from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime

SNAPSHOT_LABELS = {"vorher": "vorher", "nachher": "nachher"}


def _moment(value: str | None) -> str:
    if not value:
        return "offen"
    parsed = parse_datetime(value)
    if parsed is None:
        return str(value)
    return f"{timezone.localtime(parsed):%d.%m.%Y %H:%M}"


def _time_only(value: str | None) -> str:
    parsed = parse_datetime(value) if value else None
    if parsed is None:
        return _moment(value)
    return f"{timezone.localtime(parsed):%H:%M}"


def describe_snapshot(snapshot: dict) -> str:
    """Eine Momentaufnahme eines Zeiteintrags als ein Satz."""
    parts = [f"{_moment(snapshot.get('start'))} bis {_time_only(snapshot.get('end'))}"]
    activity = snapshot.get("activity")
    parts.append(activity if activity else "ohne Tätigkeit")

    breaks = snapshot.get("breaks")
    if breaks is not None:
        if breaks:
            times = ", ".join(
                f"{_time_only(pause.get('start'))} bis {_time_only(pause.get('end'))}"
                for pause in breaks
            )
            parts.append(f"Pausen {times}")
        else:
            parts.append("ohne Pause")

    note = snapshot.get("note")
    if note:
        parts.append(f"Notiz: {note}")
    return ", ".join(parts)


def _describe_value(value) -> str:
    """Alles, was keine Momentaufnahme eines Zeiteintrags ist.

    Unter vorher und nachher stehen nicht nur Zeiten, sondern etwa auch
    geänderte Stammdaten; Mitgliedschaften stehen als Listen darin.
    """
    if isinstance(value, dict):
        return ", ".join(f"{key}: {inner}" for key, inner in value.items())
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "nichts"
    return str(value)


def describe_changes(changes: dict | None) -> list[str]:
    """Die Zeilen, die unter einem Protokolleintrag stehen."""
    if not isinstance(changes, dict) or not changes:
        return []

    lines = []
    for key, value in changes.items():
        label = SNAPSHOT_LABELS.get(key, key)
        if isinstance(value, dict) and "start" in value:
            lines.append(f"{label}: {describe_snapshot(value)}")
        else:
            lines.append(f"{label}: {_describe_value(value)}")
    return lines
