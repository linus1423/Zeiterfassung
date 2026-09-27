"""Regeln für das Ändern von Mitgliedschaften.

Die Mitgliederseite einer Gruppe und die Nutzerverwaltung der System-Admins
ändern dieselben Mitgliedschaften. Damit beide dieselben Grenzen einhalten,
stehen die Prüfungen hier und nicht in den Views.
"""

from apps.tracking.models import TimeEntry

from .models import GroupMembership

CLOCKED_IN = "Der Nutzer ist gerade eingestempelt und kann nicht entfernt werden."
LAST_ADMIN = "Die Gruppe braucht mindestens einen Admin."


def _is_last_admin(membership: GroupMembership) -> bool:
    return (
        membership.is_admin
        and GroupMembership.objects.filter(
            group_id=membership.group_id, role=GroupMembership.Role.ADMIN
        ).count()
        == 1
    )


def removal_blocker(membership: GroupMembership) -> str | None:
    """Grund, warum die Mitgliedschaft nicht enden darf, sonst None."""
    if (
        TimeEntry.objects.open()
        .filter(user_id=membership.user_id, group_id=membership.group_id)
        .exists()
    ):
        return CLOCKED_IN
    if _is_last_admin(membership):
        return LAST_ADMIN
    return None


def role_change_blocker(membership: GroupMembership, role: str) -> str | None:
    """Grund, warum die Rolle nicht wechseln darf, sonst None."""
    if role != GroupMembership.Role.ADMIN and _is_last_admin(membership):
        return LAST_ADMIN
    return None
