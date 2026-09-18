from django.urls import reverse

from apps.groups.models import Activity, GroupMembership


def test_member_cannot_open_group_overview(client, member, group):
    client.force_login(member)

    response = client.get(reverse("groups:detail", args=[group.pk]))

    assert response.status_code == 403


def test_group_admin_can_open_group_overview(client, group_admin, group):
    client.force_login(group_admin)

    response = client.get(reverse("groups:detail", args=[group.pk]))

    assert response.status_code == 200


def test_accountant_can_read_every_group(client, accountant, group):
    client.force_login(accountant)

    response = client.get(reverse("groups:detail", args=[group.pk]))

    assert response.status_code == 200


def test_accountant_cannot_manage_activities(client, accountant, group):
    client.force_login(accountant)

    response = client.get(reverse("groups:activities", args=[group.pk]))

    assert response.status_code == 403


def test_admin_of_one_group_cannot_read_another(client, group_admin, other_group):
    client.force_login(group_admin)

    response = client.get(reverse("groups:detail", args=[other_group.pk]))

    assert response.status_code == 403


def test_admin_creates_activity(client, group_admin, group):
    client.force_login(group_admin)

    client.post(
        reverse("groups:activities", args=[group.pk]),
        {"name": "Reparatur", "description": "", "sort_order": 10, "is_active": "on"},
        follow=True,
    )

    assert Activity.objects.filter(group=group, name="Reparatur").exists()


def test_last_admin_cannot_lose_the_role(client, group_admin, group):
    client.force_login(group_admin)
    membership = GroupMembership.objects.get(user=group_admin, group=group)

    client.post(reverse("groups:member_role", args=[group.pk, membership.pk]), follow=True)

    membership.refresh_from_db()
    assert membership.role == GroupMembership.Role.ADMIN
