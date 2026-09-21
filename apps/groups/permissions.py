"""Zentrale Rechteprüfungen.

Alle Views benutzen diese Funktionen, damit die Regeln an einer Stelle stehen
und nicht in jeder View neu formuliert werden.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Group


def require_system_admin(user, message: str = "Nur System-Admins dürfen das.") -> None:
    """System-Admin ist, wer `is_superuser` ist. Sonst 403."""
    if not user.is_superuser:
        raise PermissionDenied(message)


def require_group_admin(user, group_id) -> Group:
    """Gibt die Gruppe zurück, wenn der Nutzer dort Admin ist, sonst 403."""
    group = get_object_or_404(Group, pk=group_id)
    if not user.is_group_admin(group):
        raise PermissionDenied("Nur Admins dieser Gruppe dürfen das.")
    return group


def require_group_read(user, group_id) -> Group:
    """Lesender Zugriff auf eine Gruppe: Admin der Gruppe, Buchhaltung oder System-Admin."""
    group = get_object_or_404(Group, pk=group_id)
    if user.sees_all_groups or user.is_group_admin(group):
        return group
    raise PermissionDenied("Kein Lesezugriff auf diese Gruppe.")


def readable_groups(user):
    """Gruppen, deren fremde Zeiten der Nutzer lesen darf."""
    if user.sees_all_groups:
        return Group.objects.filter(is_active=True)
    return Group.objects.filter(pk__in=user.admin_group_ids(), is_active=True)
