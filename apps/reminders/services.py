"""Erinnerungen anlegen und wieder schließen (Issue 34).

Angelegt werden sie von den Management-Kommandos, geschlossen von dort und
von den Stellen, die den Anlass beseitigen: wer ausstempelt, soll den
Hinweis nicht bis zum nächsten Lauf des Diensts vor sich haben.
"""

from __future__ import annotations

from datetime import date

from django.db.models import QuerySet
from django.utils import timezone

from .models import Reminder


def entry_key(entry_id: int) -> str:
    return f"entry:{entry_id}"


def correction_key(correction_id: int) -> str:
    return f"correction:{correction_id}"


def period_key(group_id: int, period_start: date) -> str:
    return f"period:{group_id}:{period_start.isoformat()}"


def raise_reminder(
    kind: str,
    recipient,
    subject_key: str,
    message: str,
    *,
    url: str = "",
    group=None,
) -> Reminder | None:
    """Legt die Erinnerung an und gibt sie zurück, falls sie neu ist.

    Zu einem Anlass entsteht je Empfänger genau eine Erinnerung. Gibt es sie
    schon, kommt None zurück: dann ist auch schon eine Mail rausgegangen,
    und der nächste Lauf des Diensts schweigt.
    """
    if recipient is None or not recipient.is_active:
        return None
    reminder, created = Reminder.objects.get_or_create(
        recipient=recipient,
        kind=kind,
        subject_key=subject_key,
        defaults={"message": message, "url": url, "group": group},
    )
    return reminder if created else None


def resolve_occasion(kind: str, subject_key: str) -> int:
    """Schließt die Erinnerungen aller Empfänger zu einem erledigten Anlass."""
    return (
        Reminder.objects.unresolved()
        .filter(kind=kind, subject_key=subject_key)
        .update(resolved_at=timezone.now())
    )


def resolve_except(kind: str, keep_keys) -> int:
    """Schließt alle offenen Erinnerungen dieser Art außer den genannten Anlässen.

    Damit räumt jeder Lauf eines Kommandos auf, was zwischendurch erledigt
    wurde, ohne dass an der Stelle jemand daran gedacht hat.
    """
    return (
        Reminder.objects.unresolved()
        .filter(kind=kind)
        .exclude(subject_key__in=list(keep_keys))
        .update(resolved_at=timezone.now())
    )


def open_for(user) -> QuerySet[Reminder]:
    """Die offenen Hinweise einer Person, der neueste zuerst."""
    return Reminder.objects.unresolved().for_user(user).select_related("group")


def open_count(user) -> int:
    return Reminder.objects.unresolved().for_user(user).count()


def resolve_entry(entry_id: int) -> int:
    """Wer ausstempelt, soll den Hinweis sofort los sein."""
    return resolve_occasion(Reminder.Kind.OPEN_ENTRY, entry_key(entry_id))


def resolve_correction(correction_id: int) -> int:
    """Ein entschiedener oder zurückgezogener Antrag liegt nicht mehr offen."""
    return resolve_occasion(Reminder.Kind.PENDING_CORRECTION, correction_key(correction_id))


def resolve_period(group_id: int, period_start: date) -> int:
    """Ein abgeschlossener Zeitraum braucht keine Erinnerung mehr."""
    return resolve_occasion(Reminder.Kind.PERIOD_CLOSING, period_key(group_id, period_start))
