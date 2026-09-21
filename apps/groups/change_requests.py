"""Ablauf der Anträge auf Gruppenwechsel (Issue 37).

Ein Wechsel braucht zwei Zustimmungen: erst der Admin der bisherigen Gruppe,
dann der Admin der neuen. Erst danach wird die Mitgliedschaft umgehängt. So
verliert keine Gruppe ein Mitglied, ohne davon zu wissen, und keine bekommt
eines, das sie nicht aufnehmen wollte.

Was bewusst so entschieden ist:

* Erfasste Zeiten bleiben bei der alten Gruppe. Sie stecken in deren
  Auswertung und in abgeschlossenen Zeiträumen; ein nachträgliches Umhängen
  würde bereits exportierte Monate verändern.
* In der neuen Gruppe beginnt die Person als Mitglied. Eine Admin-Rolle gilt
  je Gruppe und wird nicht mitgenommen.
* Offene Korrekturanträge bleiben bei ihrer Gruppe, deren Admins entscheiden
  sie weiter. Sie betreffen Zeiten, die dort erfasst wurden.
* Wer gerade eingestempelt ist, wechselt nicht: der laufende Eintrag gehört
  noch zur alten Gruppe und würde sonst mitten in der Schicht heimatlos.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.audit.models import AuditLog, log

from . import notifications
from .models import GroupChangeRequest, GroupMembership


class GroupChangeError(Exception):
    """Der Wechsel ist in diesem Zustand nicht möglich."""


def open_request_for(user) -> GroupChangeRequest | None:
    """Der offene Antrag des Nutzers, falls es einen gibt."""
    return (
        GroupChangeRequest.objects.filter(user=user, status__in=GroupChangeRequest.OPEN_STATUSES)
        .select_related("from_group", "to_group")
        .first()
    )


def _other_admins(group, user) -> bool:
    return (
        GroupMembership.objects.filter(group=group, role=GroupMembership.Role.ADMIN)
        .exclude(user=user)
        .exists()
    )


def _is_clocked_in(user, group) -> bool:
    from apps.tracking.models import TimeEntry

    return TimeEntry.objects.open().filter(user=user, group=group).exists()


def may_decide(user, request_obj: GroupChangeRequest) -> bool:
    """Wer darf jetzt entscheiden: ein Admin der Gruppe, die gerade dran ist.

    Der Antragsteller selbst darf das nicht, auch wenn er dort Admin ist.
    Ist er der einzige Admin, entscheidet ein System-Admin.
    """
    group = request_obj.deciding_group
    if group is None:
        return False
    if user.pk == request_obj.user_id and not user.is_superuser:
        return False
    return user.is_group_admin(group)


def decidable_requests(user, group_ids: list[int] | None = None) -> QuerySet[GroupChangeRequest]:
    """Anträge, über die der Nutzer gerade zu entscheiden hat.

    `group_ids` sind die verwalteten Gruppen. Wer sie schon kennt, reicht sie
    durch und spart die Abfrage; die Navigation tut das auf jeder Seite.
    """
    if group_ids is None:
        group_ids = user.administrated_group_ids()
    if not group_ids:
        return GroupChangeRequest.objects.none()

    requests = GroupChangeRequest.objects.filter(
        Q(status=GroupChangeRequest.Status.PENDING_SOURCE, from_group_id__in=group_ids)
        | Q(status=GroupChangeRequest.Status.PENDING_TARGET, to_group_id__in=group_ids)
    )
    if not user.is_superuser:
        requests = requests.exclude(user=user)
    return requests.select_related("user", "from_group", "to_group").order_by("created_at")


@transaction.atomic
def create_request(*, user, from_group, to_group, reason: str) -> GroupChangeRequest:
    """Legt den Antrag an, protokolliert ihn und meldet ihn der bisherigen Gruppe."""
    if from_group.pk == to_group.pk:
        raise GroupChangeError("Bisherige und neue Gruppe sind dieselbe.")
    if not reason.strip():
        raise GroupChangeError("Ein Antrag braucht eine Begründung.")
    if not to_group.is_active:
        raise GroupChangeError("In eine stillgelegte Gruppe kann niemand wechseln.")

    membership = GroupMembership.objects.filter(user=user, group=from_group).first()
    if membership is None:
        raise GroupChangeError("Du bist in dieser Gruppe kein Mitglied.")
    if GroupMembership.objects.filter(user=user, group=to_group).exists():
        raise GroupChangeError("Du bist in der neuen Gruppe bereits Mitglied.")
    if open_request_for(user) is not None:
        raise GroupChangeError("Es läuft schon ein Antrag auf Gruppenwechsel.")
    if _is_clocked_in(user, from_group):
        raise GroupChangeError("Du bist gerade eingestempelt. Beende den Eintrag zuerst.")
    if membership.is_admin and not _other_admins(from_group, user):
        raise GroupChangeError(
            "Du bist der einzige Admin der bisherigen Gruppe. "
            "Die Gruppe braucht mindestens einen Admin."
        )

    try:
        # Zwei Anträge gleichzeitig fängt die Bedingung in der Datenbank ab;
        # der eigene Sicherungspunkt hält die Transaktion dabei benutzbar.
        with transaction.atomic():
            change = GroupChangeRequest.objects.create(
                user=user, from_group=from_group, to_group=to_group, reason=reason
            )
    except IntegrityError as exc:
        raise GroupChangeError("Es läuft schon ein Antrag auf Gruppenwechsel.") from exc
    log(
        AuditLog.Action.GROUP_CHANGE_REQUESTED,
        actor=user,
        target=change,
        group=from_group,
        subject=user,
        note=f"Wechsel nach {to_group.name} beantragt.",
    )
    notifications.notify_group_change_step(change)
    return change


@transaction.atomic
def decide(
    request_obj: GroupChangeRequest, decided_by, *, approve: bool, note: str = ""
) -> GroupChangeRequest:
    """Entscheidet die Stufe, die gerade offen ist.

    Stimmt die bisherige Gruppe zu, geht der Antrag an die neue Gruppe weiter.
    Stimmt auch diese zu, wechselt die Mitgliedschaft sofort.
    """
    locked = GroupChangeRequest.objects.select_for_update().get(pk=request_obj.pk)
    if not may_decide(decided_by, locked):
        raise GroupChangeError("Dieser Antrag darf von dir nicht entschieden werden.")
    if not approve and not note.strip():
        raise GroupChangeError("Eine Ablehnung braucht eine Begründung.")

    at_source = locked.status == GroupChangeRequest.Status.PENDING_SOURCE
    now = timezone.now()
    if at_source:
        locked.source_decided_by = decided_by
        locked.source_decided_at = now
        locked.source_note = note
        locked.status = (
            GroupChangeRequest.Status.PENDING_TARGET
            if approve
            else GroupChangeRequest.Status.REJECTED
        )
    else:
        locked.target_decided_by = decided_by
        locked.target_decided_at = now
        locked.target_note = note
        locked.status = (
            GroupChangeRequest.Status.APPROVED if approve else GroupChangeRequest.Status.REJECTED
        )
        if approve:
            _move_membership(locked, decided_by)

    locked.decision_seen_at = None
    locked.save()

    log(
        AuditLog.Action.GROUP_CHANGE_APPROVED if approve else AuditLog.Action.GROUP_CHANGE_REJECTED,
        actor=decided_by,
        target=locked,
        group=locked.from_group if at_source else locked.to_group,
        subject=locked.user,
        note=note or ("Zustimmung der bisherigen Gruppe." if at_source else ""),
    )
    notifications.notify_group_change_step(locked)
    return locked


def _move_membership(locked: GroupChangeRequest, actor) -> None:
    """Hängt die Mitgliedschaft um. Läuft nur innerhalb von `decide`.

    Zwischen Antrag und zweiter Zustimmung kann sich einiges geändert haben,
    deshalb werden dieselben Bedingungen noch einmal geprüft.
    """
    if not locked.to_group.is_active:
        raise GroupChangeError("Die neue Gruppe ist stillgelegt.")

    membership = (
        GroupMembership.objects.select_for_update()
        .filter(user=locked.user, group=locked.from_group)
        .first()
    )
    if membership is None:
        raise GroupChangeError(
            f"{locked.user.full_name} ist kein Mitglied von {locked.from_group.name} mehr."
        )
    if GroupMembership.objects.filter(user=locked.user, group=locked.to_group).exists():
        raise GroupChangeError(
            f"{locked.user.full_name} ist bereits Mitglied von {locked.to_group.name}."
        )
    if _is_clocked_in(locked.user, locked.from_group):
        raise GroupChangeError(
            f"{locked.user.full_name} ist gerade eingestempelt. "
            "Der Wechsel geht erst nach dem Ausstempeln."
        )
    if membership.is_admin and not _other_admins(locked.from_group, locked.user):
        raise GroupChangeError(
            f"{locked.user.full_name} ist der einzige Admin von {locked.from_group.name}. "
            "Die Gruppe braucht mindestens einen Admin."
        )

    from_idp = membership.from_idp
    membership.delete()
    GroupMembership.objects.create(
        user=locked.user,
        group=locked.to_group,
        role=GroupMembership.Role.MEMBER,
        source=GroupMembership.Source.MANUAL,
    )
    log(
        AuditLog.Action.GROUP_CHANGE_APPROVED,
        actor=actor,
        target=locked,
        group=locked.to_group,
        subject=locked.user,
        changes={"vorher": locked.from_group.name, "nachher": locked.to_group.name},
        note=(
            "Wechsel vollzogen. Die bisherige Mitgliedschaft kam aus dem "
            "Identity-Provider und kann beim nächsten Login erneut entstehen."
            if from_idp
            else "Wechsel vollzogen."
        ),
    )


@transaction.atomic
def withdraw(request_obj: GroupChangeRequest, user) -> GroupChangeRequest:
    locked = GroupChangeRequest.objects.select_for_update().get(pk=request_obj.pk)
    if locked.user_id != user.pk:
        raise GroupChangeError("Nur der Antragsteller kann zurücknehmen.")
    if not locked.is_pending:
        raise GroupChangeError("Dieser Antrag ist bereits entschieden.")

    locked.status = GroupChangeRequest.Status.WITHDRAWN
    locked.save(update_fields=["status"])
    log(
        AuditLog.Action.GROUP_CHANGE_WITHDRAWN,
        actor=user,
        target=locked,
        group=locked.from_group,
        subject=user,
    )
    return locked


def _unseen_decisions_q(user) -> Q:
    """Eigene Anträge, deren Entscheidung der Nutzer noch nicht gesehen hat."""
    return Q(
        user=user,
        status__in=(GroupChangeRequest.Status.APPROVED, GroupChangeRequest.Status.REJECTED),
        decision_seen_at__isnull=True,
    )


def mark_decisions_seen(user) -> None:
    """Entschiedene eigene Anträge als gesehen markieren (Zähler in der Navigation)."""
    GroupChangeRequest.objects.filter(_unseen_decisions_q(user)).update(
        decision_seen_at=timezone.now()
    )


def navigation_counts(user, group_ids: list[int] | None = None) -> dict[str, int]:
    """Beide Zähler der Navigation in einer Abfrage.

    Die Navigation steht auf jeder Seite, deshalb zählt hier jede Abfrage.
    Beide Zahlen stecken in derselben Tabelle und lassen sich zusammen holen.
    """
    if group_ids is None:
        group_ids = user.administrated_group_ids()

    pending = Q(status=GroupChangeRequest.Status.PENDING_SOURCE, from_group_id__in=group_ids) | Q(
        status=GroupChangeRequest.Status.PENDING_TARGET, to_group_id__in=group_ids
    )
    if not user.is_superuser:
        pending &= ~Q(user=user)
    if not group_ids:
        pending = Q(pk__in=[])

    return GroupChangeRequest.objects.aggregate(
        pending_group_changes=Count("pk", filter=pending),
        new_group_changes=Count("pk", filter=_unseen_decisions_q(user)),
    )
