"""Mails zum Gruppenwechsel (Issue 37)."""

import pytest
from django.core import mail

from apps.groups import change_requests
from apps.groups.models import GroupMembership


@pytest.fixture
def emails_on(settings):
    settings.GROUP_CHANGE_EMAILS_ENABLED = True
    settings.SITE_BASE_URL = "https://zeiterfassung.example.com"
    return settings


@pytest.fixture
def other_admin(make_user, other_group):
    user = make_user("buero-admin@example.com", first_name="Bea", last_name="Buero")
    GroupMembership.objects.create(user=user, group=other_group, role=GroupMembership.Role.ADMIN)
    return user


def _request(member, group, other_group):
    return change_requests.create_request(
        user=member, from_group=group, to_group=other_group, reason="Ich wechsle ins Büro."
    )


def test_die_bisherige_gruppe_erfaehrt_vom_antrag(
    emails_on, member, group, other_group, group_admin, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        _request(member, group, other_group)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [group_admin.email]
    assert "Ich wechsle ins Büro." in message.body
    assert "https://zeiterfassung.example.com" in message.body


def test_die_neue_gruppe_erfaehrt_erst_nach_der_ersten_zustimmung(
    emails_on,
    member,
    group,
    other_group,
    group_admin,
    other_admin,
    django_capture_on_commit_callbacks,
):
    change = _request(member, group, other_group)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        change_requests.decide(change, group_admin, approve=True, note="Gern.")

    assert [message.to for message in mail.outbox] == [[other_admin.email]]
    assert "Gern." in mail.outbox[0].body


def test_der_antragsteller_erfaehrt_die_entscheidung(
    emails_on, member, group, other_group, group_admin, django_capture_on_commit_callbacks
):
    change = _request(member, group, other_group)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        change_requests.decide(change, group_admin, approve=False, note="Wir brauchen dich hier.")

    assert [message.to for message in mail.outbox] == [[member.email]]
    assert "Wir brauchen dich hier." in mail.outbox[0].body


def test_ohne_einstellung_keine_mail(
    member, group, other_group, group_admin, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        _request(member, group, other_group)

    assert mail.outbox == []
