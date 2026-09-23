"""Ein Zeiteintrag ohne Tätigkeit darf auf keinem Weg entstehen (Issue 83)."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.corrections import services as corrections
from apps.corrections.models import CorrectionRequest
from apps.groups.models import GroupMembership
from apps.tracking import entry_editing
from apps.tracking import services as tracking
from apps.tracking.models import TimeEntry


@pytest.fixture
def workday(member, group, activity):
    start = timezone.now() - timedelta(hours=9)
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


def test_the_model_demands_an_activity(member, group):
    entry = TimeEntry(user=member, group=group, start=timezone.now())

    with pytest.raises(ValidationError) as exc:
        entry.full_clean()

    assert "activity" in exc.value.message_dict


def test_clocking_in_without_an_activity_is_refused(member, group):
    with pytest.raises(tracking.ClockError) as exc:
        tracking.clock_in(member, group, None)

    assert "Ohne Tätigkeit" in str(exc.value)
    assert not TimeEntry.objects.exists()


def test_the_clock_page_explains_a_group_without_activities(client, make_user, other_group):
    """Ohne Tätigkeit in der Gruppe bleibt sonst nur eine leere Auswahl."""
    person = make_user("ohne@example.com")
    GroupMembership.objects.create(user=person, group=other_group)
    client.force_login(person)

    response = client.get(reverse("tracking:clock"))

    assert response.context["has_activities"] is False
    assert "noch keine Tätigkeit angelegt" in response.content.decode()


def test_a_correction_request_needs_an_activity(member, group, workday):
    with pytest.raises(corrections.CorrectionError) as exc:
        corrections.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.EDIT,
            reason="Ich habe zu spät ausgestempelt.",
            entry=workday,
            proposed_start=workday.start,
            proposed_end=workday.start + timedelta(hours=7),
        )

    assert "Tätigkeit" in str(exc.value)
    assert not CorrectionRequest.objects.exists()


def test_a_delete_request_still_works_without_one(member, group, workday):
    """Beim Löschen entsteht kein Eintrag, also braucht es auch keine Tätigkeit."""
    correction = corrections.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.DELETE,
        reason="Doppelt erfasst.",
        entry=workday,
    )

    assert correction.is_pending


def test_approving_with_changes_cannot_drop_the_activity(member, group, group_admin, workday):
    correction = corrections.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Ich habe zu spät ausgestempelt.",
        entry=workday,
        proposed_activity=workday.activity,
        proposed_start=workday.start,
        proposed_end=workday.start + timedelta(hours=7),
    )

    with pytest.raises(corrections.CorrectionError) as exc:
        corrections.approve(
            correction,
            group_admin,
            "",
            overrides={
                "start": workday.start,
                "end": workday.start + timedelta(hours=7),
                "activity": None,
            },
        )

    assert "Tätigkeit" in str(exc.value)


def test_the_admin_cannot_backdate_without_an_activity(group_admin, member, group):
    start = timezone.now() - timedelta(hours=5)

    with pytest.raises(entry_editing.EntryEditError) as exc:
        entry_editing.create_entry(
            editor=group_admin,
            user=member,
            group=group,
            reason="Nachtrag.",
            activity=None,
            start=start,
            end=start + timedelta(hours=2),
        )

    assert "Tätigkeit" in str(exc.value)


def test_the_admin_cannot_strip_the_activity_off_an_entry(group_admin, workday):
    with pytest.raises(entry_editing.EntryEditError) as exc:
        entry_editing.update_entry(
            workday,
            editor=group_admin,
            reason="Korrektur.",
            activity=None,
            start=workday.start,
            end=workday.end,
        )

    assert "Tätigkeit" in str(exc.value)
    workday.refresh_from_db()
    assert workday.activity is not None
