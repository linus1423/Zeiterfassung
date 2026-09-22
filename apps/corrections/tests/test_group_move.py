"""Gruppenwechsel eines Zeiteintrags mit zwei Zustimmungen (Issue 37)."""

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.groups.models import Activity, GroupMembership, PeriodLock
from apps.tracking.models import TimeEntry


@pytest.fixture
def yesterday_morning():
    """Feste Uhrzeit, damit der Eintrag nie über Mitternacht läuft."""
    day = timezone.localdate() - timedelta(days=1)
    return timezone.make_aware(datetime.combine(day, time(8, 0)))


@pytest.fixture
def entry(member, group, activity, yesterday_morning):
    return TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=yesterday_morning,
        end=yesterday_morning + timedelta(hours=8),
    )


@pytest.fixture
def target_group(other_group, member):
    """Die zweite eigene Gruppe des Mitglieds."""
    GroupMembership.objects.create(user=member, group=other_group)
    return other_group


@pytest.fixture
def target_activity(target_group):
    return Activity.objects.create(group=target_group, name="Beratung")


@pytest.fixture
def target_admin(make_user, target_group):
    user = make_user("zieladmin@example.com", first_name="Toni", last_name="Ziel")
    GroupMembership.objects.create(user=user, group=target_group, role=GroupMembership.Role.ADMIN)
    return user


@pytest.fixture
def move_request(member, group, entry, target_group, target_activity):
    return services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.MOVE,
        reason="Der Einsatz lief auf die Beratung.",
        entry=entry,
        proposed_group=target_group,
        proposed_activity=target_activity,
    )


def test_first_approval_only_hands_the_request_over(move_request, group_admin, entry, target_group):
    services.approve(move_request, group_admin, "Stimmt so.")

    move_request.refresh_from_db()
    entry.refresh_from_db()
    assert move_request.status == CorrectionRequest.Status.PENDING_TARGET
    assert move_request.source_decided_by == group_admin
    assert move_request.source_note == "Stimmt so."
    # Der Eintrag bleibt bis zur zweiten Zustimmung, wo er ist.
    assert entry.group_id != target_group.pk
    assert move_request.deciding_group == target_group


def test_second_approval_moves_the_entry(
    move_request, group_admin, target_admin, entry, target_group, target_activity
):
    services.approve(move_request, group_admin)
    services.approve(move_request, target_admin)

    move_request.refresh_from_db()
    entry.refresh_from_db()
    assert move_request.status == CorrectionRequest.Status.APPROVED
    assert move_request.decided_by == target_admin
    assert entry.group_id == target_group.pk
    assert entry.activity_id == target_activity.pk
    assert entry.source == TimeEntry.Source.CORRECTION
    assert AuditLog.objects.filter(
        action=AuditLog.Action.ENTRY_UPDATED, note="Gruppenwechsel aus Korrekturantrag"
    ).exists()


def test_a_move_without_an_activity_is_refused(member, group, entry, target_group):
    """Ein Zeiteintrag ohne Tätigkeit soll es nicht geben."""
    with pytest.raises(services.CorrectionError, match="fehlt die Tätigkeit"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.MOVE,
            reason="Ohne Tätigkeit.",
            entry=entry,
            proposed_group=target_group,
        )


def test_the_old_group_cannot_decide_twice(move_request, group_admin, target_group):
    services.approve(move_request, group_admin)

    move_request.refresh_from_db()
    assert services.may_decide(group_admin, move_request) is False
    with pytest.raises(services.CorrectionError):
        services.approve(move_request, group_admin)


def test_the_target_group_cannot_decide_first(move_request, target_admin):
    assert services.may_decide(target_admin, move_request) is False
    with pytest.raises(services.CorrectionError):
        services.approve(move_request, target_admin)


def test_the_target_group_may_reject(move_request, group_admin, target_admin, entry):
    services.approve(move_request, group_admin)
    services.reject(move_request, target_admin, "Gehört nicht zu uns.")

    move_request.refresh_from_db()
    entry.refresh_from_db()
    assert move_request.status == CorrectionRequest.Status.REJECTED
    assert entry.group_id == move_request.group_id


def test_requester_may_withdraw_after_the_first_approval(move_request, group_admin, member):
    services.approve(move_request, group_admin)
    services.withdraw(move_request, member)

    move_request.refresh_from_db()
    assert move_request.status == CorrectionRequest.Status.WITHDRAWN


def test_move_into_a_group_one_is_not_a_member_of_is_refused(member, group, entry, other_group):
    stranger_activity = Activity.objects.create(group=other_group, name="Fremdes")
    with pytest.raises(services.CorrectionError, match="kein Mitglied"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.MOVE,
            reason="Fremde Gruppe.",
            entry=entry,
            proposed_group=other_group,
            proposed_activity=stranger_activity,
        )


def test_move_into_the_same_group_is_refused(member, group, entry, activity):
    with pytest.raises(services.CorrectionError, match="bereits in dieser Gruppe"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.MOVE,
            reason="Steht schon da.",
            entry=entry,
            proposed_group=group,
            proposed_activity=activity,
        )


def test_an_activity_of_another_group_is_refused(member, group, entry, target_group, activity):
    with pytest.raises(services.CorrectionError, match="Tätigkeit"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.MOVE,
            reason="Falsche Tätigkeit.",
            entry=entry,
            proposed_group=target_group,
            proposed_activity=activity,
        )


def test_a_running_entry_cannot_change_group(
    member, group, target_group, target_activity, yesterday_morning
):
    running = TimeEntry.objects.create(user=member, group=group, start=yesterday_morning, end=None)
    with pytest.raises(services.CorrectionError, match="laufender"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.MOVE,
            reason="Läuft noch.",
            entry=running,
            proposed_group=target_group,
            proposed_activity=target_activity,
        )


def test_a_closed_period_in_the_target_group_blocks_the_request(
    member, group, entry, target_group, target_activity, superuser
):
    day = timezone.localtime(entry.start).date()
    PeriodLock.objects.create(
        group=target_group,
        period_start=day - timedelta(days=1),
        period_end=day + timedelta(days=1),
        closed_by=superuser,
    )
    with pytest.raises(services.CorrectionError, match="abgeschlossen"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.MOVE,
            reason="Zielgruppe ist zu.",
            entry=entry,
            proposed_group=target_group,
            proposed_activity=target_activity,
        )


def test_a_membership_lost_after_the_first_approval_stops_the_move(
    move_request, group_admin, target_admin, member, target_group, entry
):
    services.approve(move_request, group_admin)
    GroupMembership.objects.filter(user=member, group=target_group).delete()

    with pytest.raises(services.CorrectionError, match="kein Mitglied"):
        services.approve(move_request, target_admin)

    entry.refresh_from_db()
    assert entry.group_id == move_request.group_id


def test_a_deleted_entry_stops_the_move(move_request, group_admin, target_admin, entry):
    services.approve(move_request, group_admin)
    entry.delete()

    with pytest.raises(services.CorrectionError, match="gibt es nicht mehr"):
        services.approve(move_request, target_admin)


def test_the_move_cannot_be_approved_with_changes(move_request, group_admin):
    with pytest.raises(services.CorrectionError, match="geändert genehmigt"):
        services.approve(
            move_request,
            group_admin,
            overrides={"start": timezone.now(), "end": timezone.now()},
        )


def test_a_proposed_group_on_another_kind_is_refused(member, group, entry, target_group):
    with pytest.raises(services.CorrectionError, match="nur beim Gruppenwechsel"):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.EDIT,
            reason="Passt nicht.",
            entry=entry,
            proposed_start=entry.start,
            proposed_end=entry.end,
            proposed_group=target_group,
        )


def test_the_form_demands_an_activity(member, entry, target_group, target_activity):
    from apps.corrections.forms import MoveRequestForm

    form = MoveRequestForm(
        member, entry, {"target_group": target_group.pk, "reason": "Ohne Tätigkeit."}
    )

    assert form.is_valid() is False
    assert "activity" in form.errors


def test_a_group_without_activities_is_not_offered(member, entry, target_group):
    """Dorthin könnte der Eintrag nicht wechseln, also steht die Gruppe nicht zur Wahl."""
    from apps.corrections.forms import MoveRequestForm

    form = MoveRequestForm(member, entry)

    assert form.has_targets is False


def test_a_group_with_activities_is_offered(member, entry, target_group, target_activity):
    from apps.corrections.forms import MoveRequestForm

    form = MoveRequestForm(member, entry)

    assert form.has_targets is True
    assert list(form.fields["target_group"].queryset) == [target_group]
    assert list(form.fields["activity"].queryset) == [target_activity]
