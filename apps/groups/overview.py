"""Abschluss-Übersicht über mehrere Gruppen (Issue 36).

Die Frage, die den Monatslauf steuert, ist nicht "was ist abgeschlossen",
sondern "welche Gruppe hat den abgelaufenen Zeitraum noch nicht
abgeschlossen und woran hängt es dort". Diese Übersicht beantwortet sie für
alle Gruppen, die jemand lesen darf.

Die Zahlen entstehen in wenigen Abfragen für alle Gruppen zusammen und nicht
je Gruppe einzeln: die Buchhaltung sieht sonst mit jeder Gruppe eine Abfrage
mehr.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.tracking.utils import day_bounds

from .models import Group, PeriodLock
from .periods import Period, period_for

# Wie weit zurück höchstens nach einem offenen Zeitraum gesucht wird. Ohne
# Grenze liefe die Suche bei kaputten Altdaten (ein Eintrag von 1970) sehr
# lange; zehn Jahre sind mehr, als die Aufbewahrungsfrist vorsieht.
MAX_PERIODS = 120


@dataclass(frozen=True)
class ClosingRow:
    """Eine Zeile der Übersicht."""

    group: Group
    last_lock: PeriodLock | None
    pending: Period | None
    open_requests: int
    incomplete_entries: int

    @property
    def is_up_to_date(self) -> bool:
        return self.pending is None

    @property
    def has_open_work(self) -> bool:
        return bool(self.open_requests or self.incomplete_entries)


def _locks_by_group(groups) -> dict[int, list[PeriodLock]]:
    locks: dict[int, list[PeriodLock]] = {}
    for lock in PeriodLock.objects.filter(group__in=groups).order_by("period_start"):
        locks.setdefault(lock.group_id, []).append(lock)
    return locks


def _first_day_by_group(groups) -> dict[int, date]:
    """Der erste Tag mit einer Zeit je Gruppe. Davor gibt es nichts abzuschließen."""
    rows = (
        Group.objects.filter(pk__in=[group.pk for group in groups])
        .annotate(first_start=Min("time_entries__start"))
        .values_list("pk", "first_start")
    )
    return {
        group_id: timezone.localtime(first_start).date()
        for group_id, first_start in rows
        if first_start is not None
    }


def _pending_period(group: Group, locks: list[PeriodLock], first_day: date, today: date):
    """Der älteste abgelaufene Zeitraum der Gruppe ohne Abschluss."""
    period = period_for(first_day, group.month_start_day)
    for _ in range(MAX_PERIODS):
        if period.end >= today:
            return None
        covered = any(
            lock.period_start <= period.end and lock.period_end >= period.start for lock in locks
        )
        if not covered:
            return period
        period = period.next()
    return None


def _window_filter(periods: dict[int, Period], start_field: str, end_field: str | None) -> Q:
    """Ein Filter über alle Gruppen mit ihrem jeweils eigenen Zeitraum."""
    combined = Q(pk__in=[])
    for group_id, period in periods.items():
        window_start, _ = day_bounds(period.start)
        _, window_end = day_bounds(period.end)
        if end_field is None:
            combined |= Q(**{"group_id": group_id, f"{start_field}__gte": window_start}) & Q(
                **{f"{start_field}__lt": window_end}
            )
        else:
            # Auch Einträge, die vor dem Zeitraum beginnen und hineinlaufen.
            combined |= (
                Q(group_id=group_id)
                & Q(**{f"{start_field}__lt": window_end})
                & (Q(**{f"{end_field}__isnull": True}) | Q(**{f"{end_field}__gt": window_start}))
            )
    return combined


def _open_requests(periods: dict[int, Period]) -> dict[int, int]:
    from apps.corrections.models import CorrectionRequest

    if not periods:
        return {}
    by_proposal = _window_filter(periods, "proposed_start", None)
    by_entry = _window_filter(periods, "time_entry__start", None)
    rows = (
        CorrectionRequest.objects.filter(status=CorrectionRequest.Status.PENDING)
        .filter(by_proposal | by_entry)
        .values_list("group_id")
        .annotate(count=Count("pk", distinct=True))
    )
    return dict(rows)


def _incomplete_entries(periods: dict[int, Period]) -> dict[int, int]:
    from apps.tracking.models import TimeEntry

    if not periods:
        return {}
    rows = (
        TimeEntry.objects.filter(is_incomplete=True)
        .filter(_window_filter(periods, "start", "end"))
        .values_list("group_id")
        .annotate(count=Count("pk"))
    )
    return dict(rows)


def closing_overview(groups, today: date | None = None) -> list[ClosingRow]:
    """Je Gruppe: letzter Abschluss, ältester offener Zeitraum und was dort liegt."""
    groups = list(groups)
    if not groups:
        return []

    today = today or timezone.localdate()
    locks = _locks_by_group(groups)
    first_days = _first_day_by_group(groups)

    pending: dict[int, Period] = {}
    for group in groups:
        first_day = first_days.get(group.pk)
        if first_day is None:
            continue
        period = _pending_period(group, locks.get(group.pk, []), first_day, today)
        if period is not None:
            pending[group.pk] = period

    requests = _open_requests(pending)
    incomplete = _incomplete_entries(pending)

    return [
        ClosingRow(
            group=group,
            last_lock=locks.get(group.pk, [])[-1] if locks.get(group.pk) else None,
            pending=pending.get(group.pk),
            open_requests=requests.get(group.pk, 0),
            incomplete_entries=incomplete.get(group.pk, 0),
        )
        for group in groups
    ]
