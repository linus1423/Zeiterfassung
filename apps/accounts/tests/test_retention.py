"""Aufbewahrung und Anonymisierung (Issue 6)."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from apps.accounts import retention
from apps.audit.models import AuditLog
from apps.corrections.models import CorrectionRequest
from apps.tracking.models import TimeEntry


def _long_ago(days=900):
    return timezone.now() - timedelta(days=days)


def _old_account(make_user, group, email="alt@example.com"):
    user = make_user(email, first_name="Otto", last_name="Alt", personnel_number="4711")
    user.date_joined = _long_ago()
    user.save(update_fields=["date_joined"])
    return user


def test_an_account_without_recent_entries_is_a_candidate(make_user, group, activity):
    user = _old_account(make_user, group)
    start = _long_ago()
    TimeEntry.objects.create(
        user=user, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )

    found = retention.candidates()

    assert [candidate.user for candidate in found] == [user]


def test_a_fresh_account_without_entries_is_left_alone(make_user, group):
    make_user("neu@example.com")

    assert retention.candidates() == []


def test_a_recent_entry_protects_the_account(make_user, group, activity):
    user = _old_account(make_user, group)
    start = timezone.now() - timedelta(days=3)
    TimeEntry.objects.create(
        user=user, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )

    assert retention.candidates() == []


def test_a_recent_login_protects_the_account(make_user, group):
    user = _old_account(make_user, group)
    user.last_login = timezone.now() - timedelta(days=2)
    user.save(update_fields=["last_login"])

    assert retention.candidates() == []


def test_an_open_request_protects_the_account(make_user, group):
    user = _old_account(make_user, group)
    start = _long_ago()
    CorrectionRequest.objects.create(
        requested_by=user,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        proposed_start=start,
        proposed_end=start + timedelta(hours=8),
        reason="Nachtrag",
    )

    assert retention.candidates() == []


def test_a_system_admin_is_never_a_candidate(superuser, group):
    superuser.date_joined = _long_ago()
    superuser.save(update_fields=["date_joined"])

    assert retention.candidates() == []


def test_anonymizing_keeps_the_times_but_drops_the_person(make_user, group, activity):
    user = _old_account(make_user, group)
    start = _long_ago()
    entry = TimeEntry.objects.create(
        user=user, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )

    retention.anonymize(user)

    user.refresh_from_db()
    entry.refresh_from_db()
    assert entry.duration == timedelta(hours=8)
    assert user.personnel_number == ""
    assert user.first_name == ""
    assert "Otto" not in user.email
    assert user.is_active is False
    assert not user.has_usable_password()
    assert not user.group_memberships.exists()
    assert AuditLog.objects.filter(action=AuditLog.Action.USER_ANONYMIZED).exists()


def test_an_anonymized_account_is_not_picked_up_twice(make_user, group):
    user = _old_account(make_user, group)
    retention.anonymize(user)

    assert retention.candidates() == []


def test_the_retention_period_is_configurable(make_user, group, activity, settings):
    user = _old_account(make_user, group)
    start = timezone.now() - timedelta(days=100)
    TimeEntry.objects.create(
        user=user, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )

    assert retention.candidates() == []
    assert [candidate.user for candidate in retention.candidates(months=3)] == [user]


def test_the_command_changes_nothing_without_apply(make_user, group):
    user = _old_account(make_user, group)
    output = StringIO()

    call_command("anonymize_expired_users", stdout=output)

    user.refresh_from_db()
    assert user.personnel_number == "4711"
    assert "Nichts geändert" in output.getvalue()


def test_the_command_anonymizes_with_apply(make_user, group):
    user = _old_account(make_user, group)
    output = StringIO()

    call_command("anonymize_expired_users", "--apply", stdout=output)

    user.refresh_from_db()
    assert user.personnel_number == ""
    assert "1 Konten anonymisiert" in output.getvalue()
