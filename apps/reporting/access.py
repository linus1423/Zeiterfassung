"""Wer die Auswertung, ihre Vorlagen und ihre Zeitpläne sehen und ändern darf.

Die Regeln stehen hier zusammen, weil sie zweimal gebraucht werden: beim
Anlegen eines Zeitplans im Browser und beim Ausführen im Dienst `scheduler`.
Ein Plan, der später mit zu großen Rechten liefe, wird dort erneut geprüft.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import ExportProfile, ExportSchedule


def may_use_reporting(user) -> bool:
    """Auswertung sehen: Buchhaltung, System-Admin oder Admin mindestens einer Gruppe."""
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.sees_all_groups or user.is_any_group_admin)
    )


def require_reporting_access(user) -> None:
    """Wie `may_use_reporting`, nur für Views: sonst 403."""
    if not may_use_reporting(user):
        raise PermissionDenied("Kein Zugriff auf die Auswertung.")


def visible_profiles(user):
    """Vorlagen, die der Nutzer benutzen darf: eigene und geteilte der Buchhaltung."""
    return ExportProfile.objects.filter(
        Q(owner=user) | Q(is_shared=True, owner__is_accounting=True)
    ).select_related("owner")


def may_schedule(user, profile: ExportProfile) -> bool:
    """Einen Zeitplan zu dieser Vorlage anlegen und ausführen lassen."""
    if not may_use_reporting(user):
        return False
    return visible_profiles(user).filter(pk=profile.pk).exists()


def visible_schedules(user):
    """Zeitpläne, die der Nutzer sehen darf.

    Eigene, die zu eigenen Vorlagen, und für die Buchhaltung alle.
    """
    return (
        ExportSchedule.objects.visible_to(user)
        .select_related("profile", "profile__owner", "created_by", "period_group")
        .prefetch_related("runs")
    )


def may_change_schedule(user, schedule: ExportSchedule) -> bool:
    """Ändern und löschen darf, wer den Plan angelegt hat oder die Vorlage besitzt.

    Die Buchhaltung darf es ebenfalls.
    """
    return bool(
        schedule.created_by_id == user.pk
        or schedule.profile.owner_id == user.pk
        or user.sees_all_groups
    )
