"""Ablauf der Korrekturanträge.

Genehmigen ändert den Zeiteintrag und schreibt ins Protokoll. Der Antrag
bleibt als Beleg erhalten.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog, log
from apps.groups import closing
from apps.tracking.entries import (
    apply_breaks,
    lock_user,
    overlap_message,
    overlapping_entry,
    snapshot,
)
from apps.tracking.models import TimeEntry

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


def affected_days(request_obj: CorrectionRequest, overrides: dict | None = None) -> list:
    """Die Tage, die ein Antrag berührt: bisheriger und gewünschter Zeitpunkt."""
    values = [request_obj.proposed_start, request_obj.proposed_end]
    if request_obj.time_entry_id and request_obj.time_entry is not None:
        values.append(request_obj.time_entry.start)
    if overrides:
        values.extend([overrides.get("start"), overrides.get("end")])
    return [timezone.localtime(value).date() for value in values if value is not None]


def closed_period_lock(request_obj: CorrectionRequest, overrides: dict | None = None):
    """Der Abschluss, der diesen Antrag blockiert, falls es einen gibt (Issue 5)."""
    return closing.blocking_lock(request_obj.group, affected_days(request_obj, overrides))


def _reject_overlap(user, start, end, *, exclude_id=None) -> None:
    """Doppelt erfasste Zeit fällt später niemandem mehr auf, also hier prüfen."""
    clash = overlapping_entry(user, start, end, exclude_id=exclude_id)
    if clash is not None:
        raise CorrectionError(overlap_message(clash))


def _resolved_overrides(locked: CorrectionRequest, overrides: dict) -> dict:
    """Prüft die Werte, mit denen ein Admin einen Antrag geändert übernimmt.

    Was nicht genannt ist, bleibt wie beantragt. Ein ausdrückliches None oder
    eine leere Liste löschen dagegen, sonst könnte der Aufrufer Tätigkeit und
    Pausen nicht mehr entfernen.
    """
    if locked.kind == CorrectionRequest.Kind.DELETE:
        raise CorrectionError("Ein Löschantrag kann nicht geändert genehmigt werden.")

    resolved = {
        "start": overrides.get("start", locked.proposed_start),
        "end": overrides.get("end", locked.proposed_end),
        "activity": overrides.get("activity", locked.proposed_activity),
        "breaks": overrides.get("breaks", locked.proposed_breaks),
    }
    if resolved["start"] is None or resolved["end"] is None:
        raise CorrectionError("Für die Übernahme mit Änderung fehlen Beginn oder Ende.")
    if resolved["end"] <= resolved["start"]:
        raise CorrectionError("Das Ende muss nach dem Beginn liegen.")
    activity = resolved["activity"]
    if activity is not None and activity.group_id != locked.group_id:
        raise CorrectionError("Die Tätigkeit gehört zu einer anderen Gruppe.")
    return resolved


def _record_applied(locked: CorrectionRequest, overrides: dict) -> None:
    """Hält fest, was statt des Beantragten übernommen wurde (Issue 31)."""
    locked.applied_start = overrides["start"]
    locked.applied_end = overrides["end"]
    locked.applied_activity = overrides["activity"]
    locked.applied_breaks = overrides["breaks"] or []


@transaction.atomic
def approve(
    request_obj: CorrectionRequest,
    decided_by,
    note: str = "",
    overrides: dict | None = None,
) -> CorrectionRequest:
    """Genehmigt einen Antrag, auf Wunsch mit geänderten Zeiten (Issue 31).

    `overrides` enthält start, end, activity und breaks. Damit übernimmt ein
    Admin einen fast richtigen Antrag, statt ihn abzulehnen und neu stellen
    zu lassen.
    """
    locked = CorrectionRequest.objects.select_for_update().get(pk=request_obj.pk)
    if not may_decide(decided_by, locked):
        raise CorrectionError("Dieser Antrag darf von dir nicht entschieden werden.")
    if overrides:
        overrides = _resolved_overrides(locked, overrides)

    # Der Abschluss kann zwischen Antrag und Entscheidung gesetzt worden sein.
    lock = closed_period_lock(locked, overrides)
    if lock is not None:
        raise CorrectionError(
            f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
            "Die Zeit kann nicht mehr geändert werden."
        )

    if locked.kind == CorrectionRequest.Kind.DELETE:
        entry = locked.time_entry
        if entry is not None:
            before = snapshot(entry)
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
        start = overrides["start"] if overrides else locked.proposed_start
        end = overrides["end"] if overrides else locked.proposed_end
        activity = overrides["activity"] if overrides else locked.proposed_activity
        breaks = overrides["breaks"] if overrides else locked.proposed_breaks
        lock_user(locked.requested_by)
        _reject_overlap(locked.requested_by, start, end)
        entry = TimeEntry.objects.create(
            user=locked.requested_by,
            group=locked.group,
            activity=activity,
            start=start,
            end=end,
            source=TimeEntry.Source.CORRECTION,
        )
        apply_breaks(entry, breaks)
        locked.time_entry = entry
        log(
            AuditLog.Action.ENTRY_UPDATED,
            actor=decided_by,
            target=entry,
            group=locked.group,
            subject=locked.requested_by,
            changes={"nachher": snapshot(entry)},
            note="Nachtrag aus Korrekturantrag",
        )
    else:
        # Der Eintrag kann zwischen Antrag und Entscheidung verschwunden sein,
        # etwa durch einen genehmigten Löschantrag auf denselben Eintrag.
        entry = (
            TimeEntry.objects.select_for_update().filter(pk=locked.time_entry_id).first()
            if locked.time_entry_id
            else None
        )
        if entry is None:
            raise CorrectionError(
                "Den Zeiteintrag gibt es nicht mehr. Der Antrag kann nur noch abgelehnt werden."
            )
        before = snapshot(entry)
        if overrides:
            entry.start = overrides["start"]
            entry.end = overrides["end"]
            entry.activity = overrides["activity"]
        else:
            entry.start = locked.proposed_start or entry.start
            entry.end = locked.proposed_end or entry.end
            if locked.proposed_activity_id:
                entry.activity = locked.proposed_activity
        entry.source = TimeEntry.Source.CORRECTION
        entry.is_incomplete = False
        lock_user(entry.user)
        _reject_overlap(entry.user, entry.start, entry.end, exclude_id=entry.pk)
        try:
            entry.full_clean(exclude=["user", "group"])
        except ValidationError as exc:
            raise CorrectionError(
                "Die gewünschte Zeit ist nicht gültig: " + "; ".join(exc.messages)
            ) from exc
        entry.save()
        apply_breaks(entry, overrides["breaks"] if overrides else locked.proposed_breaks)
        log(
            AuditLog.Action.ENTRY_UPDATED,
            actor=decided_by,
            target=entry,
            group=locked.group,
            subject=locked.requested_by,
            changes={"vorher": before, "nachher": snapshot(entry)},
        )

    if overrides:
        _record_applied(locked, overrides)
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
        note="Mit Änderung genehmigt." if overrides else "",
    )
    notifications.notify_requester_of_decision(locked)
    return locked


@transaction.atomic
def reject(request_obj: CorrectionRequest, decided_by, note: str) -> CorrectionRequest:
    locked = CorrectionRequest.objects.select_for_update().get(pk=request_obj.pk)
    if not may_decide(decided_by, locked):
        raise CorrectionError("Dieser Antrag darf von dir nicht entschieden werden.")
    if not note.strip():
        raise CorrectionError("Eine Ablehnung braucht eine Begründung.")

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
        raise CorrectionError("Nur der Antragsteller kann zurücknehmen.")
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
    # Über die Gruppe laufen zwei Dinge: wer entscheiden darf und welcher
    # Abschluss sperrt. Ein bestehender Eintrag gibt sie deshalb vor, sonst
    # entschiede ein Admin einer fremden Gruppe über fremde Zeiten.
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
            "Korrekturen sind dort nicht mehr möglich."
        )

    if kind != CorrectionRequest.Kind.DELETE:
        _reject_overlap(
            requested_by,
            proposed_start,
            proposed_end,
            exclude_id=entry.pk if entry is not None else None,
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
    """Entschiedene eigene Anträge als gesehen markieren (Zähler in der Navigation)."""
    CorrectionRequest.objects.filter(
        requested_by=user,
        status__in=(CorrectionRequest.Status.APPROVED, CorrectionRequest.Status.REJECTED),
        decision_seen_at__isnull=True,
    ).update(decision_seen_at=timezone.now())
