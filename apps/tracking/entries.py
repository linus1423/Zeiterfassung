"""Gemeinsame Bausteine für das Ändern von Zeiteinträgen.

Zeiten werden an zwei Stellen geändert: über einen genehmigten Korrekturantrag
und direkt durch einen Gruppen-Admin (Issue 31). Beide Wege müssen dieselben
Regeln anwenden, deshalb stehen die Bausteine hier und nicht in einer der
beiden Anwendungen.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import BreakEntry, TimeEntry


def snapshot(entry: TimeEntry) -> dict:
    """Der Zustand eines Eintrags für das Protokoll.

    Pausen und Notiz gehören dazu, weil ein Admin auch nur sie ändern kann;
    ohne sie stünde im Protokoll vorher und nachher dasselbe.
    """
    return {
        "start": entry.start.isoformat(),
        "end": entry.end.isoformat() if entry.end else None,
        "activity": entry.activity.name if entry.activity else None,
        "note": entry.note,
        "breaks": [
            {"start": pause.start.isoformat(), "end": pause.end.isoformat()}
            for pause in entry.breaks.order_by("start")
            if pause.end is not None
        ],
    }


def lock_user(user) -> None:
    """Sperrt die Zeiten eines Nutzers für die Dauer der Transaktion.

    Die Prüfung auf Überschneidung liest nur; ohne diese Sperre könnten zwei
    Admins gleichzeitig prüfen, beide nichts finden und sich überschneidende
    Zeiten schreiben. Gesperrt wird der Nutzer, weil die Zeile, mit der sich
    die neue Zeit überschneiden würde, noch gar nicht existieren muss.
    """
    # first() statt exists(), damit die Abfrage wirklich als SELECT ... FOR
    # UPDATE auf der Nutzerzeile ankommt.
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()


def apply_breaks(entry: TimeEntry, items: list[dict] | None) -> None:
    """Ersetzt die Pausen eines Eintrags durch die übergebenen."""
    entry.breaks.all().delete()
    for item in items or []:
        start = item.get("start")
        end = item.get("end")
        if isinstance(start, str):
            start = parse_datetime(start)
        if isinstance(end, str):
            end = parse_datetime(end)
        if start and end and end > start:
            BreakEntry.objects.create(time_entry=entry, start=start, end=end)


def overlapping_entry(user, start, end, *, exclude_id=None) -> TimeEntry | None:
    """Ein anderer Zeiteintrag desselben Nutzers im gewünschten Zeitraum."""
    if start is None or end is None:
        return None
    queryset = TimeEntry.objects.overlapping(user, start, end)
    if exclude_id is not None:
        queryset = queryset.exclude(pk=exclude_id)
    return queryset.order_by("start").first()


def overlap_message(clash: TimeEntry) -> str:
    """Meldung zu einer doppelt erfassten Zeit, gleich formuliert an allen Stellen."""
    local = timezone.localtime(clash.start)
    return (
        "Die gewünschte Zeit überschneidet sich mit einem anderen Zeiteintrag "
        f"vom {local:%d.%m.%Y} ab {local:%H:%M} Uhr."
    )
