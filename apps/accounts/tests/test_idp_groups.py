"""Gruppen aus den Claims des Identity-Providers (Issue 4)."""

import pytest

from apps.accounts.idp_groups import claim_names, sync_memberships
from apps.audit.models import AuditLog
from apps.groups.models import GroupMembership


@pytest.fixture
def sync_on(settings):
    settings.OIDC_GROUP_SYNC = True
    settings.OIDC_GROUPS_CLAIM = "groups"
    settings.OIDC_GROUP_SYNC_MODE = "add"
    settings.OIDC_ADMIN_GROUPS_CLAIM = ""
    settings.OIDC_ADMIN_GROUP_SUFFIX = ""
    return settings


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["Werkstatt"], {"werkstatt"}),
        ("Werkstatt, Buero", {"werkstatt", "buero"}),
        (["/werk/werkstatt"], {"werk/werkstatt", "werkstatt"}),
        ([""], set()),
        (None, set()),
        (42, set()),
    ],
)
def test_claims_are_read_in_every_shape(value, expected):
    assert claim_names({"groups": value}, "groups") == expected


def test_nothing_happens_without_the_switch(make_user, group):
    user = make_user("neu@example.com")

    assert sync_memberships(user, {"groups": ["Werkstatt"]}) == {}
    assert not user.group_memberships.exists()


def test_a_membership_is_created_from_the_claim(sync_on, make_user, group):
    user = make_user("neu@example.com")

    summary = sync_memberships(user, {"groups": ["Werkstatt"]})

    membership = user.group_memberships.get()
    assert membership.group == group
    assert membership.role == GroupMembership.Role.MEMBER
    assert membership.source == GroupMembership.Source.IDP
    assert summary["neu"]
    assert AuditLog.objects.filter(action=AuditLog.Action.MEMBERSHIP_SYNCED).exists()


def test_the_idp_identifier_wins_over_the_name(sync_on, make_user, group):
    group.idp_identifier = "AD-Gruppe-Werkstatt"
    group.save(update_fields=["idp_identifier"])
    user = make_user("neu@example.com")

    sync_memberships(user, {"groups": ["AD-Gruppe-Werkstatt"]})

    assert user.group_memberships.filter(group=group).exists()


def test_an_unknown_group_is_ignored(sync_on, make_user, group):
    user = make_user("neu@example.com")

    sync_memberships(user, {"groups": ["Vertrieb"]})

    assert not user.group_memberships.exists()


def test_add_mode_keeps_what_the_claim_does_not_mention(sync_on, member, group, other_group):
    sync_memberships(member, {"groups": ["Buero"]})

    groups = set(member.group_memberships.values_list("group__name", flat=True))
    assert groups == {"Werkstatt", "Buero"}


def test_replace_mode_only_removes_what_it_created(sync_on, member, group, other_group):
    sync_on.OIDC_GROUP_SYNC_MODE = "replace"
    sync_memberships(member, {"groups": ["Buero"]})

    # Die Mitgliedschaft in "Buero" kommt vom Provider, die in "Werkstatt"
    # wurde im Tool gepflegt und bleibt deshalb.
    summary = sync_memberships(member, {"groups": []})

    groups = set(member.group_memberships.values_list("group__name", flat=True))
    assert groups == {"Werkstatt"}
    assert summary["entzogen"] == ["Buero"]


def test_a_manual_membership_is_not_changed(sync_on, member, group):
    sync_on.OIDC_ADMIN_GROUPS_CLAIM = "admin_groups"

    sync_memberships(member, {"groups": ["Werkstatt"], "admin_groups": ["Werkstatt"]})

    membership = member.group_memberships.get()
    assert membership.role == GroupMembership.Role.MEMBER
    assert membership.source == GroupMembership.Source.MANUAL


def test_the_admin_role_comes_from_its_own_claim(sync_on, make_user, group):
    sync_on.OIDC_ADMIN_GROUPS_CLAIM = "admin_groups"
    user = make_user("neu@example.com")

    sync_memberships(user, {"groups": ["Werkstatt"], "admin_groups": ["Werkstatt"]})

    assert user.group_memberships.get().role == GroupMembership.Role.ADMIN


def test_the_admin_role_can_follow_a_naming_convention(sync_on, make_user, group):
    sync_on.OIDC_ADMIN_GROUP_SUFFIX = "-admins"
    user = make_user("neu@example.com")

    sync_memberships(user, {"groups": ["werkstatt-admins"]})

    assert user.group_memberships.get().role == GroupMembership.Role.ADMIN


def test_an_idp_role_is_lowered_again(sync_on, make_user, group, group_admin):
    sync_on.OIDC_ADMIN_GROUPS_CLAIM = "admin_groups"
    user = make_user("neu@example.com")
    sync_memberships(user, {"groups": ["Werkstatt"], "admin_groups": ["Werkstatt"]})

    summary = sync_memberships(user, {"groups": ["Werkstatt"], "admin_groups": []})

    assert user.group_memberships.get().role == GroupMembership.Role.MEMBER
    assert summary["geaendert"]


def test_the_last_admin_keeps_the_role(sync_on, make_user, group):
    sync_on.OIDC_ADMIN_GROUPS_CLAIM = "admin_groups"
    user = make_user("allein@example.com")
    sync_memberships(user, {"groups": ["Werkstatt"], "admin_groups": ["Werkstatt"]})

    sync_memberships(user, {"groups": ["Werkstatt"], "admin_groups": []})

    assert user.group_memberships.get().role == GroupMembership.Role.ADMIN


def test_an_inactive_group_is_left_alone(sync_on, make_user, group):
    group.is_active = False
    group.save(update_fields=["is_active"])
    user = make_user("neu@example.com")

    sync_memberships(user, {"groups": ["Werkstatt"]})

    assert not user.group_memberships.exists()
