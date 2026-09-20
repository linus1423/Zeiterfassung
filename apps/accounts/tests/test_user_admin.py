"""Nutzerstammdaten: Personalnummer und Rolle Buchhaltung (Issue 29)."""

from django.urls import reverse

from apps.audit.models import AuditLog


def test_user_list_is_only_for_system_admins(client, group_admin):
    client.force_login(group_admin)

    response = client.get(reverse("accounts:user_list"))

    assert response.status_code == 403


def test_user_list_shows_accounts(client, superuser, member):
    client.force_login(superuser)

    response = client.get(reverse("accounts:user_list"))

    assert response.status_code == 200
    assert member.full_name in response.content.decode()


def test_user_list_can_be_searched(client, superuser, member):
    client.force_login(superuser)

    response = client.get(reverse("accounts:user_list"), {"q": "mitglied"})

    assert [person.pk for person in response.context["users"]] == [member.pk]


def test_personnel_number_can_be_saved(client, superuser, member):
    client.force_login(superuser)

    client.post(
        reverse("accounts:user_edit", args=[member.pk]),
        {"personnel_number": "  4711 ", "is_accounting": ""},
        follow=True,
    )

    member.refresh_from_db()
    assert member.personnel_number == "4711"
    assert member.is_accounting is False
    assert AuditLog.objects.filter(action=AuditLog.Action.USER_UPDATED, subject=member).exists()


def test_accounting_role_can_be_granted(client, superuser, member):
    client.force_login(superuser)

    client.post(
        reverse("accounts:user_edit", args=[member.pk]),
        {"personnel_number": "", "is_accounting": "on"},
        follow=True,
    )

    member.refresh_from_db()
    assert member.is_accounting is True


def test_personnel_number_stays_unique(client, superuser, member, make_user):
    other = make_user("zweite@example.com", personnel_number="4711")
    client.force_login(superuser)

    response = client.post(
        reverse("accounts:user_edit", args=[member.pk]),
        {"personnel_number": "4711", "is_accounting": ""},
    )

    member.refresh_from_db()
    assert response.status_code == 200
    assert member.personnel_number == ""
    assert other.full_name in response.content.decode()


def test_group_admin_cannot_edit_accounts(client, group_admin, member):
    client.force_login(group_admin)

    response = client.post(
        reverse("accounts:user_edit", args=[member.pk]), {"personnel_number": "4711"}
    )

    member.refresh_from_db()
    assert response.status_code == 403
    assert member.personnel_number == ""
