"""System-Admins setzen die Gruppenzugehörigkeit eines Kontos."""

from django.urls import reverse

from apps.audit.models import AuditLog
from apps.groups.models import GroupMembership
from apps.tracking.services import clock_in


def _post(client, person, **choices):
    data = {f"gruppe_{group.pk}": role for group, role in choices.values()}
    return client.post(reverse("accounts:user_groups", args=[person.pk]), data)


def test_only_system_admins_may_set_groups(client, group_admin, member, group):
    client.force_login(group_admin)

    response = _post(client, member, a=(group, "admin"))

    assert response.status_code == 403
    assert GroupMembership.objects.get(user=member, group=group).role == "member"


def test_get_is_not_allowed(client, superuser, member):
    client.force_login(superuser)

    response = client.get(reverse("accounts:user_groups", args=[member.pk]))

    assert response.status_code == 405


def test_edit_page_shows_a_choice_per_group(client, superuser, member, group, other_group):
    client.force_login(superuser)

    response = client.get(reverse("accounts:user_edit", args=[member.pk]))

    form = response.context["groups_form"]
    assert form[f"gruppe_{group.pk}"].initial == "member"
    assert form[f"gruppe_{other_group.pk}"].initial == ""


def test_add_change_and_remove(client, superuser, member, group, other_group, group_admin):
    client.force_login(superuser)

    response = _post(client, member, a=(group, ""), b=(other_group, "admin"))

    assert response.status_code == 302
    assert not GroupMembership.objects.filter(user=member, group=group).exists()
    neu = GroupMembership.objects.get(user=member, group=other_group)
    assert neu.role == "admin"
    assert neu.source == GroupMembership.Source.MANUAL
    assert neu.joined_at is not None
    eintrag = AuditLog.objects.get(action=AuditLog.Action.USER_UPDATED, subject=member)
    assert eintrag.changes == {
        "vorher": {"Werkstatt": "Mitglied"},
        "nachher": {"Buero": "Gruppen-Admin"},
    }


def test_unchanged_form_writes_no_log(client, superuser, member, group, other_group):
    client.force_login(superuser)

    _post(client, member, a=(group, "member"), b=(other_group, ""))

    assert not AuditLog.objects.filter(subject=member).exists()


def test_last_admin_stays(client, superuser, group_admin, group, other_group):
    client.force_login(superuser)

    response = _post(client, group_admin, a=(group, "member"), b=(other_group, "member"))

    assert response.status_code == 200
    assert "mindestens einen Admin" in response.content.decode()
    assert GroupMembership.objects.get(user=group_admin, group=group).is_admin
    # Nichts halb gespeichert: auch die zweite Gruppe bleibt unberührt.
    assert not GroupMembership.objects.filter(user=group_admin, group=other_group).exists()


def test_clocked_in_member_cannot_be_removed(client, superuser, member, group, activity):
    clock_in(member, group, activity)
    client.force_login(superuser)

    response = _post(client, member, a=(group, ""))

    assert response.status_code == 200
    assert "eingestempelt" in response.content.decode()
    assert GroupMembership.objects.filter(user=member, group=group).exists()


def test_inactive_groups_are_left_alone(client, superuser, member, other_group):
    other_group.is_active = False
    other_group.save()
    GroupMembership.objects.create(user=member, group=other_group)
    client.force_login(superuser)

    response = client.get(reverse("accounts:user_edit", args=[member.pk]))

    assert f"gruppe_{other_group.pk}" not in response.context["groups_form"].fields
    _post(client, member)
    assert GroupMembership.objects.filter(user=member, group=other_group).exists()


def test_group_missing_from_form_is_not_touched(client, superuser, member, group):
    client.force_login(superuser)

    _post(client, member)

    assert GroupMembership.objects.filter(user=member, group=group).exists()
