"""Sichtbarkeit gespeicherter Export-Vorlagen (Issue 72).

Geteilt wird auf zwei getrennten Wegen: an die Admins der eigenen Gruppen und
an die Buchhaltung. Wer beides ist, entscheidet je Weg.
"""

import pytest
from django.urls import reverse

from apps.groups.models import Group, GroupMembership
from apps.reporting.access import visible_profiles
from apps.reporting.models import ExportProfile


def make_profile(owner, name="Monatsabrechnung", **flags):
    return ExportProfile.objects.create(
        owner=owner,
        name=name,
        columns=["full_name", "hours"],
        grouping=ExportProfile.Grouping.ENTRY,
        filters={},
        **flags,
    )


@pytest.fixture
def second_group_admin(make_user, group):
    """Zweiter Admin derselben Gruppe wie `group_admin`."""
    user = make_user("admin2@example.com", first_name="Bea", last_name="Beisitz")
    GroupMembership.objects.create(user=user, group=group, role=GroupMembership.Role.ADMIN)
    return user


@pytest.fixture
def foreign_group_admin(make_user, other_group):
    user = make_user("fremd@example.com", first_name="Cem", last_name="Fremd")
    GroupMembership.objects.create(user=user, group=other_group, role=GroupMembership.Role.ADMIN)
    return user


def test_own_profile_is_always_visible(group_admin):
    profile = make_profile(group_admin)

    assert list(visible_profiles(group_admin)) == [profile]


def test_unshared_profile_stays_private(group_admin, second_group_admin, accountant):
    make_profile(group_admin)

    assert not visible_profiles(second_group_admin).exists()
    assert not visible_profiles(accountant).exists()


def test_group_admin_shares_with_the_other_admins_of_the_group(group_admin, second_group_admin):
    profile = make_profile(group_admin, share_with_group_admins=True)

    assert list(visible_profiles(second_group_admin)) == [profile]


def test_group_admin_sharing_does_not_reach_a_foreign_group(group_admin, foreign_group_admin):
    make_profile(group_admin, share_with_group_admins=True)

    assert not visible_profiles(foreign_group_admin).exists()


def test_group_admin_sharing_does_not_reach_plain_members(group_admin, member):
    make_profile(group_admin, share_with_group_admins=True)

    assert not visible_profiles(member).exists()


def test_group_admin_sharing_does_not_reach_accounting(group_admin, accountant):
    """Die Buchhaltung bekommt die Vorlage nur über den eigenen Haken."""
    make_profile(group_admin, share_with_group_admins=True)

    assert not visible_profiles(accountant).exists()


def test_group_admin_shares_with_accounting(group_admin, accountant):
    profile = make_profile(group_admin, share_with_accounting=True)

    assert list(visible_profiles(accountant)) == [profile]


def test_accounting_sharing_does_not_reach_group_admins(accountant, group_admin):
    """Der Fehler aus Issue 72: bisher sah das jeder Gruppen-Admin."""
    make_profile(accountant, share_with_accounting=True)

    assert not visible_profiles(group_admin).exists()


def test_accounting_shares_with_other_accountants(accountant, make_user):
    other = make_user("buch2@example.com", is_accounting=True)
    profile = make_profile(accountant, share_with_accounting=True)

    assert list(visible_profiles(other)) == [profile]


def test_both_flags_reach_both_sides(group_admin, second_group_admin, accountant):
    profile = make_profile(group_admin, share_with_group_admins=True, share_with_accounting=True)

    assert list(visible_profiles(second_group_admin)) == [profile]
    assert list(visible_profiles(accountant)) == [profile]


def test_a_profile_shows_up_once_despite_two_shared_groups(
    group_admin, second_group_admin, other_group
):
    """Zwei gemeinsame Gruppen dürfen die Vorlage nicht verdoppeln."""
    for user in (group_admin, second_group_admin):
        GroupMembership.objects.create(
            user=user, group=other_group, role=GroupMembership.Role.ADMIN
        )
    make_profile(group_admin, share_with_group_admins=True)

    assert visible_profiles(second_group_admin).count() == 1


def test_inactive_group_does_not_share(group_admin, second_group_admin, group):
    make_profile(group_admin, share_with_group_admins=True)
    Group.objects.filter(pk=group.pk).update(is_active=False)

    assert not visible_profiles(second_group_admin).exists()


def test_superuser_sees_profiles_shared_with_group_admins(group_admin, superuser):
    """Der System-Admin verwaltet alle Gruppen, siehe `administrated_group_ids`."""
    profile = make_profile(group_admin, share_with_group_admins=True)

    assert list(visible_profiles(superuser)) == [profile]


def test_saving_a_profile_stores_both_flags(client, group_admin):
    client.force_login(group_admin)

    response = client.post(
        reverse("reporting:export"),
        {
            "start": "2026-01-01",
            "end": "2026-01-31",
            "grouping": "entry",
            "columns": ["full_name", "hours"],
            "csv_dialect": "de",
            "action": "save_profile",
            "name": "Monatsabrechnung",
            "share_with_accounting": "on",
        },
    )

    assert response.status_code == 302
    profile = ExportProfile.objects.get(name="Monatsabrechnung")
    assert profile.share_with_accounting is True
    assert profile.share_with_group_admins is False


def test_sharing_label_names_both_ways(group_admin):
    profile = make_profile(group_admin, share_with_group_admins=True, share_with_accounting=True)

    assert profile.sharing_label == "Gruppen-Admins, Buchhaltung"
    assert make_profile(group_admin, name="Still").sharing_label == ""
