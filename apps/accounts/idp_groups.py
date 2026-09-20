"""Gruppenzugehörigkeit aus den Claims des Identity-Providers (Issue 4).

Die offenen Punkte aus dem Issue sind so entschieden:

* Welcher Claim: `OIDC_GROUPS_CLAIM`, Vorgabe `groups`. Verglichen wird gegen
  Kurzname, Name und das Feld "Bezeichnung beim Identity-Provider" der Gruppe.
  Keycloak liefert verschachtelte Pfade wie `/werk/werkstatt`; dann passt auch
  das letzte Pfadstück.
* Im Tool gepflegte Mitgliedschaften bleiben unangetastet. Der Provider legt
  eigene Mitgliedschaften an und ändert auch nur diese. Bei
  `OIDC_GROUP_SYNC_MODE=replace` entzieht er sie wieder, sonst ergänzt er nur.
* Die Admin-Rolle kommt nur aus dem Token, wenn das ausdrücklich konfiguriert
  ist: entweder über einen eigenen Claim (`OIDC_ADMIN_GROUPS_CLAIM`) oder
  über eine Namenskonvention (`OIDC_ADMIN_GROUP_SUFFIX`, etwa `-admins`).

Ohne `OIDC_GROUP_SYNC=true` passiert hier nichts.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db import transaction

from apps.audit.models import AuditLog, log
from apps.groups.models import Group, GroupMembership

logger = logging.getLogger(__name__)

_SEPARATORS = re.compile(r"[,;\s]+")


def _candidate_names(value: str) -> set[str]:
    """Bezeichnungen, unter denen ein Claim-Wert auf eine Gruppe passen kann."""
    text = str(value).strip()
    if not text:
        return set()
    names = {text.strip("/")}
    if "/" in text:
        names.add(text.rstrip("/").rsplit("/", 1)[-1])
    return {name.casefold() for name in names if name}


def claim_names(claims: dict, claim: str) -> set[str]:
    """Die Gruppenbezeichnungen eines Claims, normalisiert."""
    if not claim or not isinstance(claims, dict):
        return set()

    value = claims.get(claim)
    if value is None:
        return set()
    if isinstance(value, str):
        raw = [part for part in _SEPARATORS.split(value) if part]
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        return set()

    names: set[str] = set()
    for item in raw:
        names |= _candidate_names(item)
    return names


def desired_roles(claims: dict) -> dict[int, str]:
    """Welche Gruppe mit welcher Rolle laut Token gilt."""
    member_names = claim_names(claims, settings.OIDC_GROUPS_CLAIM)
    admin_names = claim_names(claims, settings.OIDC_ADMIN_GROUPS_CLAIM)
    suffix = (settings.OIDC_ADMIN_GROUP_SUFFIX or "").strip().casefold()

    wanted: dict[int, str] = {}
    for group in Group.objects.filter(is_active=True):
        names = group.idp_names
        is_member = bool(names & member_names)
        is_admin = bool(names & admin_names)
        if suffix and {name + suffix for name in names} & member_names:
            is_admin = True
        if is_admin:
            wanted[group.pk] = GroupMembership.Role.ADMIN
        elif is_member:
            wanted[group.pk] = GroupMembership.Role.MEMBER
    return wanted


def _remaining_admins(group_id: int, exclude_user_id: int) -> int:
    return (
        GroupMembership.objects.filter(group_id=group_id, role=GroupMembership.Role.ADMIN)
        .exclude(user_id=exclude_user_id)
        .count()
    )


@transaction.atomic
def sync_memberships(user, claims: dict) -> dict[str, list[str]]:
    """Gleicht die Mitgliedschaften eines Nutzers mit dem Token ab."""
    if not settings.OIDC_GROUP_SYNC:
        return {}

    wanted = desired_roles(claims)
    existing = {
        membership.group_id: membership
        for membership in GroupMembership.objects.select_related("group").filter(user=user)
    }
    summary: dict[str, list[str]] = {"neu": [], "geändert": [], "entzogen": []}

    for group_id, role in wanted.items():
        membership = existing.get(group_id)
        if membership is None:
            created = GroupMembership.objects.create(
                user=user,
                group_id=group_id,
                role=role,
                source=GroupMembership.Source.IDP,
            )
            summary["neu"].append(f"{created.group.name} ({created.get_role_display()})")
        elif membership.from_idp and membership.role != role:
            if membership.role == GroupMembership.Role.ADMIN and not _remaining_admins(
                group_id, user.pk
            ):
                # Die Gruppe braucht mindestens einen Admin.
                continue
            membership.role = role
            membership.save(update_fields=["role"])
            summary["geändert"].append(f"{membership.group.name} ({membership.get_role_display()})")

    if settings.OIDC_GROUP_SYNC_MODE == "replace":
        for group_id, membership in existing.items():
            if group_id in wanted or not membership.from_idp:
                continue
            if membership.is_admin and not _remaining_admins(group_id, user.pk):
                continue
            summary["entzogen"].append(membership.group.name)
            membership.delete()

    if any(summary.values()):
        log(
            AuditLog.Action.MEMBERSHIP_SYNCED,
            actor=user,
            subject=user,
            changes=summary,
            note="Mitgliedschaften aus dem Identity-Provider übernommen.",
        )
    return summary


def claims_from_sociallogin(sociallogin) -> dict:
    """Die Claims eines Logins, unabhängig davon, wo allauth sie ablegt."""
    account = getattr(sociallogin, "account", None)
    data = getattr(account, "extra_data", None)
    return data if isinstance(data, dict) else {}
