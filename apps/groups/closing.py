"""Monatsabschluss: Zeiträume gegen nachträgliche Korrekturen sperren.

Ein Abschluss gilt immer für eine Gruppe und einen Abrechnungszeitraum
dieser Gruppe. Solange er besteht, nimmt das System für diesen Zeitraum
keine Korrekturen mehr an. Abschließen darf ein Admin der Gruppe,
wieder öffnen nur ein System-Admin.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditLog, log

from .models import Group, PeriodLock
from .periods import Period, group_period


class ClosingError(Exception):
    """Der Abschluss kann so nicht gesetzt oder aufgehoben werden."""


def may_close(user, group: Group) -> bool:
    return user.is_group_admin(group)


def may_reopen(user) -> bool:
    """Einen Abschluss aufheben darf nur ein System-Admin."""
    return bool(user.is_superuser)


def lock_for(group: Group, day: date) -> PeriodLock | None:
    """Der Abschluss, der diesen Tag dieser Gruppe sperrt, falls es einen gibt."""
    return PeriodLock.objects.filter(
        group=group, period_start__lte=day, period_end__gte=day
    ).first()


def is_closed(group: Group, day: date) -> bool:
    return lock_for(group, day) is not None


def lock_in_range(group: Group, first: date, last: date) -> PeriodLock | None:
    """Der erste Abschluss, der einen Tag von first bis last sperrt, beide einschließlich."""
    return PeriodLock.objects.filter(
        group=group, period_start__lte=last, period_end__gte=first
    ).first()


def blocking_lock(group: Group, ranges: Iterable[tuple[date, date] | None]) -> PeriodLock | None:
    """Der erste Abschluss, der einen der Tagesbereiche sperrt.

    Geprüft wird jeder Bereich ganz und nicht nur seine Enden: ein Eintrag
    kann über Mitternacht laufen, und zwischen seinem ersten und letzten Tag
    kann ein abgeschlossener Zeitraum liegen.
    """
    for entry_range in ranges:
        if entry_range is None:
            continue
        lock = lock_in_range(group, *entry_range)
        if lock is not None:
            return lock
    return None


def lock_exists(group: Group, period: Period) -> bool:
    return PeriodLock.objects.filter(group=group, period_start=period.start).exists()


def overlapping_lock(group: Group, period: Period) -> PeriodLock | None:
    """Ein Abschluss, der sich mit diesem Zeitraum überschneidet.

    Nach einer Änderung des Zyklus passen alte Abschlüsse nicht mehr auf die
    neuen Zeiträume; sie gelten aber weiter für die Tage, die sie sperren.
    """
    return PeriodLock.objects.filter(
        group=group, period_start__lte=period.end, period_end__gte=period.start
    ).first()


@transaction.atomic
def close_period(group: Group, period: Period, user, note: str = "") -> PeriodLock:
    """Schließt einen Zeitraum ab. Ein laufender Zeitraum bleibt offen."""
    if not may_close(user, group):
        raise ClosingError("Nur Admins dieser Gruppe dürfen einen Zeitraum abschließen.")
    if period.end >= timezone.localdate():
        raise ClosingError("Ein Zeitraum kann erst nach seinem Ende abgeschlossen werden.")

    existing = overlapping_lock(group, period)
    if existing is not None:
        raise ClosingError(
            f"Der Zeitraum {existing.period.label} ist bereits abgeschlossen "
            "und überschneidet sich damit."
        )

    try:
        lock = PeriodLock.objects.create(
            group=group,
            period_start=period.start,
            period_end=period.end,
            closed_by=user,
            note=note,
        )
    except IntegrityError as exc:
        raise ClosingError("Dieser Zeitraum ist bereits abgeschlossen.") from exc

    log(
        AuditLog.Action.PERIOD_CLOSED,
        actor=user,
        target=lock,
        group=group,
        note=f"{period.label} abgeschlossen." + (f" {note}" if note else ""),
    )
    return lock


@transaction.atomic
def reopen_period(lock: PeriodLock, user, note: str = "") -> None:
    if not may_reopen(user):
        raise ClosingError("Einen Abschluss kann nur ein System-Admin wieder öffnen.")

    group = lock.group
    label = lock.period.label
    lock.delete()
    log(
        AuditLog.Action.PERIOD_REOPENED,
        actor=user,
        group=group,
        note=f"{label} wieder geöffnet." + (f" {note}" if note else ""),
    )


class ClosedPeriods:
    """Alle Abschlüsse einmal laden, danach ohne weitere Abfragen nachsehen.

    Der Export prüft jede Zeile, deshalb wird nicht je Zeile abgefragt.
    """

    def __init__(self, group_ids: Iterable[int] | None = None):
        queryset = PeriodLock.objects.all()
        if group_ids is not None:
            queryset = queryset.filter(group_id__in=list(group_ids))
        self._by_group: dict[int, list[tuple[date, date]]] = {}
        for group_id, start, end in queryset.values_list("group_id", "period_start", "period_end"):
            self._by_group.setdefault(group_id, []).append((start, end))

    def is_closed(self, group_id: int, day: date | None) -> bool:
        if day is None:
            return False
        return any(start <= day <= end for start, end in self._by_group.get(group_id, ()))


def period_overview(group: Group, count: int = 12, today: date | None = None) -> list[dict]:
    """Die letzten Zeiträume einer Gruppe samt Abschluss, für die Anzeige."""
    from .periods import recent_periods

    periods = recent_periods(group, count=count, today=today)
    locks = list(
        PeriodLock.objects.filter(group=group, period_end__gte=periods[-1].start).select_related(
            "closed_by"
        )
    )
    current = group_period(group, today)

    def lock_for_period(period: Period) -> PeriodLock | None:
        # Genau passend, sonst einer, der sich überschneidet: nach einer
        # Zyklusänderung sperren alte Abschlüsse weiter ihre Tage.
        exact = next((lock for lock in locks if lock.period_start == period.start), None)
        if exact is not None:
            return exact
        return next(
            (
                lock
                for lock in locks
                if lock.period_start <= period.end and lock.period_end >= period.start
            ),
            None,
        )

    return [
        {
            "period": period,
            "lock": lock_for_period(period),
            "is_current": period.start == current.start,
        }
        for period in periods
    ]
