"""Gruppen anlegen (Issue 10)."""

from django.urls import reverse

from apps.audit.models import AuditLog
from apps.groups.models import Group


def test_a_system_admin_creates_a_group(client, superuser):
    client.force_login(superuser)

    response = client.post(
        reverse("groups:create"),
        {"name": "Vertrieb", "cost_center": "4712", "month_start_day": 1, "idp_identifier": ""},
        follow=True,
    )

    group = Group.objects.get(name="Vertrieb")
    assert response.status_code == 200
    assert group.slug == "vertrieb"
    assert AuditLog.objects.filter(action=AuditLog.Action.GROUP_CREATED).exists()
    # Weiter geht es bei den Mitgliedern, eine Gruppe ohne Menschen nützt nichts.
    assert response.redirect_chain[-1][0] == reverse("groups:members", args=[group.pk])


def test_a_group_admin_may_not_create_a_group(client, group_admin):
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:create"),
        {"name": "Vertrieb", "cost_center": "", "month_start_day": 1, "idp_identifier": ""},
    )

    assert response.status_code == 403
    assert not Group.objects.filter(name="Vertrieb").exists()


def test_the_accounting_may_not_create_a_group(client, accountant):
    client.force_login(accountant)

    assert client.get(reverse("groups:create")).status_code == 403


def test_a_name_that_exists_is_refused(client, superuser, group):
    client.force_login(superuser)

    response = client.post(
        reverse("groups:create"),
        {"name": "werkstatt", "cost_center": "", "month_start_day": 1, "idp_identifier": ""},
    )

    assert response.status_code == 200
    assert "gibt es schon" in response.content.decode()
    assert Group.objects.count() == 1


def test_a_name_with_the_same_short_name_is_refused(client, superuser, group):
    client.force_login(superuser)

    response = client.post(
        reverse("groups:create"),
        {"name": "Werkstatt!", "cost_center": "", "month_start_day": 1, "idp_identifier": ""},
    )

    assert response.status_code == 200
    assert "Kurzname" in response.content.decode()
    assert Group.objects.count() == 1


def test_a_cycle_outside_the_range_is_refused(client, superuser):
    client.force_login(superuser)

    client.post(
        reverse("groups:create"),
        {"name": "Vertrieb", "cost_center": "", "month_start_day": 30, "idp_identifier": ""},
    )

    assert not Group.objects.exists()


def test_the_system_admin_sees_the_button_without_any_group(client, superuser):
    client.force_login(superuser)

    response = client.get(reverse("groups:list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("groups:create") in content
    assert "Es gibt noch keine Gruppe." in content


def test_the_group_menu_shows_up_without_any_group(client, superuser):
    client.force_login(superuser)

    response = client.get(reverse("tracking:clock"))

    assert reverse("groups:list") in response.content.decode()


def test_a_member_sees_no_button(client, member):
    client.force_login(member)

    response = client.get(reverse("groups:list"))

    assert reverse("groups:create") not in response.content.decode()
