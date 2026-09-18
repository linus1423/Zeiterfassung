"""Ablauf der Korrekturantraege.

Genehmigen aendert den Zeiteintrag und schreibt ins Protokoll. Der Antrag
bleibt als Beleg erhalten.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.models import AuditLog, log
from apps.tracking.models import BreakEntry, TimeEntry

from .models import CorrectionRequest


class CorrectionError(Exception):
    """Der Antrag kann in diesem Zustand nicht bearbeitet werden."""


def may_decide(user, request_obj: CorrectionRequest) -> bool:
    """Wer darf entscheiden: ein Admin der Gruppe, aber nicht der Antragsteller.

    Hat eine Gruppe nur einen Admin und stellt dieser selbst einen Antrag,
    entscheidet ein System-Admin.
    """
    if not request_obj.is_pending:
        return False
    if user.pk == request_obj.requested_by_id and not user.is_superuser:
        return False
    return user.is_group_admin(request_obj.group)


def _entry_snapshot(entry: TimeEntry) -> dict:
    return {
        "start": entry.start.isoformat(),
        "end": entry.end.isoformat() if entry.end else None,
        "activity": entry.activity.name if entry.activity else None,
    }


def _apply_breaks(entry: TimeEntry, proposed: list[dict]) -> None:
    """Ersetzt die Pausen eines Eintrags durch die beantragten."""
    entry.breaks.all().delete()
    for item in proposed or []:
        start = parse_datetime(item.get("start") or "")
        end = parse_datetime(item.get("end") or "")
        if start and end and end > start:
            BreakEntry.objects.create(time_entry=entry, start=start, end=end)


@transaction.atomic
def approve(request_obj: CorrectionRequest, decided_by, note: str = "") -> CorrectionRequest:
    locked = CorrectionRequest.objects.select_for_update().get(pk=request_obj.pk)
    if not may_decide(decided_by, locked):
        raise CorrectionError("Dieser Antrag darf von dir nicht entschieden werden.")

    if locked.kind == CorrectionRequest.Kind.DELETE:
        entry = locked.time_entry
        if entry is not None:
            before = _entry_snapshot(entry)
            log(
                AuditLog.Action.ENTRY_DELETED,
                actor=decided_by,
                target=entry,
                group=locked.group,
                subject=locked.requested_by,
                changes={"vorher": before},
            )
            entry.delete()
            locked.time_entry = None
    elif locked.kind == CorrectionRequest.Kind.CREATE:
        entry = TimeEntry.objects.create(
            user=locked.requested_by,
            group=locked.group,
            activity=locked.proposed_activity,
            start=locked.proposed_start,
            end=locked.proposed_end,
            source=TimeEntry.Source.CORRECTION,
        )
        _apply_breaks(entry, locked.proposed_breaks)
        locked.time_entry = entry
        log(
            AuditLog.Action.ENTRY_UPDATED,
            actor=decided_by,
            target=entry,
            group=locked.group,
            subject=locked.requested_by,
            changes={"nachher": _entry_snapshot(entry)},
            note="Nachtrag aus Korrekturantrag",
        )
    else:
        entry = TimeEntry.objects.select_for_update().get(pk=locked.time_entry_id)
        before = _entry_snapshot(entry)
        entry.start = locked.proposed_start or entry.start
        entry.end = locked.proposed_end or entry.end
        if locked.proposed_activity_id:
            entry.activity = locked.proposed_activity
        entry.source = TimeEntry.Source.CORRECTION
        entry.is_incomplete = False
        try:
            entry.full_clean(exclude=["user", "group"])
        except ValidationError as exc:
            raise CorrectionError(
                "Die gewuenschte Zeit ist nicht gueltig: " + "; ".join(exc.messages)
            ) from exc
        entry.save()
        _apply_breaks(entry, locked.proposed_breaks)
        log(
            AuditLog.Action.ENTRY_UPDATED,
            actor=decided_by,
            target=entry,
            group=locked.group,
            subject=locked.requested_by,
            changes={"vorher": before, "nachher": _entry_snapshot(entry)},
        )

    locked.status = CorrectionRequest.Status.APPROVED
    locked.decided_by = decided_by
    locked.decided_at = timezone.now()
    locked.decision_note = note
    locked.save()

    log(
        AuditLog.Action.CORRECTION_APPROVED,
        actor=decided_by,
        target=locked,
        group=locked.group,
        subject=locked.requested_by,
    )
    return locked


@transaction.atomic
def reject(request_obj: CorrectionRequest, decided_by, note: str) -> CorrectionRequest:
    locked = CorrectionRequest.objects.select_for_update().get(pk=request_obj.pk)
    if not may_decide(decided_by, locked):
        raise CorrectionError("Dieser Antrag darf von dir nicht entschieden werden.")
    if not note.strip():
        raise CorrectionError("Eine Ablehnung braucht eine Begruendung.")

    locked.status = CorrectionRequest.Status.REJECTED
    locked.decided_by = decided_by
    locked.decided_at = timezone.now()
    locked.decision_note = note
    locked.save()

    log(
        AuditLog.Action.CORRECTION_REJECTED,
        actor=decided_by,
        target=locked,
        group=locked.group,
        subject=locked.requested_by,
        note=note,
    )
    return locked


@transaction.atomic
def withdraw(request_obj: CorrectionRequest, user) -> CorrectionRequest:
    locked = CorrectionRequest.objects.select_for_update().get(pk=request_obj.pk)
    if locked.requested_by_id != user.pk:
        raise CorrectionError("Nur der Antragsteller kann zuruecknehmen.")
    if not locked.is_pending:
        raise CorrectionError("Dieser Antrag ist bereits entschieden.")

    locked.status = CorrectionRequest.Status.WITHDRAWN
    locked.save(update_fields=["status"])
    log(
        AuditLog.Action.CORRECTION_WITHDRAWN,
        actor=user,
        target=locked,
        group=locked.group,
        subject=user,
    )
    return locked
