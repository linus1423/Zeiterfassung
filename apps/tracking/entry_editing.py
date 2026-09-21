"""Direktes Ändern von Zeiten durch einen Gruppen-Admin (Issue 31).

Kapitel 5 der Spezifikation: "Ein Admin kann Zeiten seiner Gruppe direkt
ändern, das wird aber ebenfalls protokolliert." Ohne diesen Weg müsste ein
Admin einen fast richtigen Antrag ablehnen und neu stellen lassen, und für
jemanden, der krank ist, könnte niemand nachtragen.

Es gelten dieselben Regeln wie beim genehmigten Korrekturantrag: der
Abschluss eines Zeitraums sperrt, Zeiten dürfen sich nicht überschneiden,
und jede Änderung steht mit vorher und nachher im Protokoll.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditLog, log
from apps.groups import closing

from .entries import (
    apply_breaks,
    lock_user,
    overlap_guard,
    overlap_message,
    overlapping_entry,
    snapshot,
)
from .models import TimeEntry
from .utils import local_day_range


class EntryEditError(Exception):
    """Die Zeit kann so nicht geändert werden."""


def may_edit(user, group) -> bool:
    """Ändern darf ein Admin der Gruppe. Die Buchhaltung liest nur."""
    return user.is_group_admin(group)


def _require_admin(editor, group) -> None:
    if not may_edit(editor, group):
        raise EntryEditError("Nur Admins dieser Gruppe dürfen Zeiten ändern.")


def _require_open_period(group, ranges) -> None:
    lock = closing.blocking_lock(group, ranges)
    if lock is not None:
        raise EntryEditError(
            f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
            "Die Zeit kann nicht mehr geändert werden."
        )


def _require_free_slot(user, start, end, *, exclude_id=None) -> None:
    clash = overlapping_entry(user, start, end, exclude_id=exclude_id)
    if clash is not None:
        raise EntryEditError(overlap_message(clash))


def _validate(entry: TimeEntry) -> None:
    try:
        entry.full_clean(exclude=["user", "group"])
    except ValidationError as exc:
        raise EntryEditError("Die Zeit ist nicht gültig: " + "; ".join(exc.messages)) from exc


@transaction.atomic
def update_entry(
    entry: TimeEntry,
    *,
    editor,
    reason: str,
    start,
    end,
    activity=None,
    note: str = "",
    breaks: list[dict] | None = None,
) -> TimeEntry:
    """Ändert einen bestehenden Eintrag. Die Begründung ist Pflicht."""
    if not reason.strip():
        raise EntryEditError("Eine Änderung braucht eine Begründung.")

    locked = TimeEntry.objects.select_for_update().select_related("group").get(pk=entry.pk)
    _require_admin(editor, locked.group)
    lock_user(locked.user)
    if locked.is_open:
        raise EntryEditError("Ein laufender Eintrag kann nicht geändert werden.")
    if activity is not None and activity.group_id != locked.group_id:
        raise EntryEditError("Die Tätigkeit gehört zu einer anderen Gruppe.")

    # Der bisherige und der gewünschte Zeitraum, jeder ganz: der Eintrag kann
    # über Mitternacht laufen, und er kann aus einem Zeitraum in einen anderen
    # verschoben werden.
    _require_open_period(
        locked.group,
        [local_day_range(locked.start, locked.end), local_day_range(start, end)],
    )
    _require_free_slot(locked.user, start, end, exclude_id=locked.pk)

    before = snapshot(locked)
    locked.start = start
    locked.end = end
    locked.activity = activity
    locked.note = note
    locked.source = TimeEntry.Source.CORRECTION
    locked.is_incomplete = False
    _validate(locked)
    with overlap_guard(EntryEditError):
        locked.save()
    apply_breaks(locked, breaks)

    log(
        AuditLog.Action.ENTRY_UPDATED,
        actor=editor,
        target=locked,
        group=locked.group,
        subject=locked.user,
        changes={"vorher": before, "nachher": snapshot(locked)},
        note=f"Direkt geändert: {reason.strip()}",
    )
    return locked


@transaction.atomic
def create_entry(
    *,
    editor,
    user,
    group,
    reason: str,
    start,
    end,
    activity=None,
    note: str = "",
    breaks: list[dict] | None = None,
) -> TimeEntry:
    """Trägt eine fehlende Zeit für ein Mitglied nach."""
    if not reason.strip():
        raise EntryEditError("Ein Nachtrag braucht eine Begründung.")

    _require_admin(editor, group)
    lock_user(user)
    if not user.is_group_member(group):
        raise EntryEditError("Die Person ist kein Mitglied dieser Gruppe.")
    if activity is not None and activity.group_id != group.pk:
        raise EntryEditError("Die Tätigkeit gehört zu einer anderen Gruppe.")

    _require_open_period(group, [local_day_range(start, end)])
    _require_free_slot(user, start, end)

    entry = TimeEntry(
        user=user,
        group=group,
        activity=activity,
        start=start,
        end=end,
        note=note,
        source=TimeEntry.Source.CORRECTION,
    )
    _validate(entry)
    with overlap_guard(EntryEditError):
        entry.save()
    apply_breaks(entry, breaks)

    log(
        AuditLog.Action.ENTRY_UPDATED,
        actor=editor,
        target=entry,
        group=group,
        subject=user,
        changes={"nachher": snapshot(entry)},
        note=f"Direkt nachgetragen: {reason.strip()}",
    )
    return entry
