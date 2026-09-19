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
from apps.groups import closing
from apps.tracking.models import BreakEntry, TimeEntry

from . import notifications
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


def affected_days(request_obj: CorrectionRequest) -> list:
    """Die Tage, die ein Antrag beruehrt: bisheriger und gewuenschter Zeitpunkt."""
    values = [request_obj.proposed_start, request_obj.proposed_end]
    if request_obj.time_entry_id and request_obj.time_entry is not None:
        values.append(request_obj.time_entry.start)
    return [timezone.localtime(value).date() for value in values if value is not None]


def closed_period_lock(request_obj: CorrectionRequest):
    """Der Abschluss, der diesen Antrag blockiert, falls es einen gibt (Issue 5)."""
    return closing.blocking_lock(request_obj.group, affected_days(request_obj))


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

    # Der Abschluss kann zwischen Antrag und Entscheidung gesetzt worden sein.
    lock = closed_period_lock(locked)
    if lock is not None:
        raise CorrectionError(
            f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
            "Die Zeit kann nicht mehr geaendert werden."
        )

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
    locked.decision_seen_at = None
    locked.save()

    log(
        AuditLog.Action.CORRECTION_APPROVED,
        actor=decided_by,
        target=locked,
        group=locked.group,
        subject=locked.requested_by,
    )
    notifications.notify_requester_of_decision(locked)
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
    locked.decision_seen_at = None
    locked.save()

    log(
        AuditLog.Action.CORRECTION_REJECTED,
        actor=decided_by,
        target=locked,
        group=locked.group,
        subject=locked.requested_by,
        note=note,
    )
    notifications.notify_requester_of_decision(locked)
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


@transaction.atomic
def create_request(
    *,
    requested_by,
    group,
    kind: str,
    reason: str,
    entry: TimeEntry | None = None,
    proposed_start=None,
    proposed_end=None,
    proposed_activity=None,
    proposed_breaks: list[dict] | None = None,
) -> CorrectionRequest:
    """Legt einen Antrag an, protokolliert ihn und meldet ihn den Admins."""
    # Ueber die Gruppe laufen zwei Dinge: wer entscheiden darf und welcher
    # Abschluss sperrt. Ein bestehender Eintrag gibt sie deshalb vor, sonst
    # entschiede ein Admin einer fremden Gruppe ueber fremde Zeiten.
    if entry is not None and entry.group_id != group.pk:
        raise CorrectionError("Ein bestehender Eintrag bleibt in seiner Gruppe.")

    correction = CorrectionRequest(
        time_entry=entry,
        requested_by=requested_by,
        group=group,
        kind=kind,
        proposed_start=proposed_start,
        proposed_end=proposed_end,
        proposed_activity=proposed_activity,
        proposed_breaks=proposed_breaks or [],
        reason=reason,
    )

    lock = closed_period_lock(correction)
    if lock is not None:
        raise CorrectionError(
            f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
            "Korrekturen sind dort nicht mehr moeglich."
        )

    correction.save()
    log(
        AuditLog.Action.CORRECTION_REQUESTED,
        actor=requested_by,
        target=correction,
        group=group,
        subject=requested_by,
    )
    notifications.notify_admins_of_new_request(correction)
    return correction


def mark_decisions_seen(user) -> None:
    """Entschiedene eigene Antraege als gesehen markieren (Zaehler in der Navigation)."""
    CorrectionRequest.objects.filter(
        requested_by=user,
        status__in=(CorrectionRequest.Status.APPROVED, CorrectionRequest.Status.REJECTED),
        decision_seen_at__isnull=True,
    ).update(decision_seen_at=timezone.now())
