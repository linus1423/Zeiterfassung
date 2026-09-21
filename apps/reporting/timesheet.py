"""Arbeitszeitnachweis je Person und Zeitraum (Issue 35).

Ein Blatt mit einer Zeile je Tag, zum Ausdrucken und Unterschreiben. Tage
ohne Erfassung bleiben leer, damit Lücken sichtbar sind. Gerechnet wird
über die Tagesanteile aus `apps.tracking.daysplit`, damit eine Nachtschicht
auf dem Blatt genauso zählt wie in der Auswertung (Issue 32).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Prefetch

from apps.groups import closing
from apps.groups.models import Group
from apps.groups.periods import WEEKDAY_NAMES
from apps.tracking.daysplit import DayPart, entries_in_range, parts_in_range
from apps.tracking.models import BreakEntry, TimeEntry

# Ein Blatt deckt höchstens diesen Zeitraum ab. Ein Nachweis ist ein Monats-
# blatt; alles darüber wäre weder druckbar noch sinnvoll zu unterschreiben.
MAX_DAYS = 366


@dataclass(frozen=True)
class DayRow:
    """Ein Tag auf dem Nachweis."""

    day: date
    parts: list[DayPart]

    @property
    def weekday(self) -> str:
        return WEEKDAY_NAMES[self.day.weekday()]

    @property
    def is_empty(self) -> bool:
        return not self.parts

    @property
    def start(self):
        return self.parts[0].start if self.parts else None

    @property
    def end(self):
        return self.parts[-1].end if self.parts else None

    @property
    def work(self) -> timedelta:
        return sum((part.work for part in self.parts), timedelta())

    @property
    def pause(self) -> timedelta:
        """Alles zwischen Beginn und Ende, was keine Arbeitszeit ist.

        Das sind die erfassten Pausen und die Lücken zwischen mehreren
        Einträgen eines Tages. So geht die Zeile auf: Ende minus Beginn
        minus Pause ergibt die Arbeitszeit.
        """
        if not self.parts:
            return timedelta()
        value = (self.end - self.start) - self.work
        return value if value > timedelta() else timedelta()

    @property
    def is_incomplete(self) -> bool:
        """Wurde an diesem Tag ein Eintrag automatisch beendet?"""
        return any(part.entry.is_incomplete for part in self.parts)


@dataclass(frozen=True)
class Timesheet:
    """Das fertige Blatt einer Person."""

    user: object
    first_day: date
    last_day: date
    rows: list[DayRow]
    groups: list[Group]
    closed: bool
    open_entries: int

    @property
    def total(self) -> timedelta:
        return sum((row.work for row in self.rows), timedelta())

    @property
    def days_worked(self) -> int:
        return sum(1 for row in self.rows if not row.is_empty)

    @property
    def has_incomplete(self) -> bool:
        return any(row.is_incomplete for row in self.rows)


def may_see(viewer, person) -> bool:
    """Wer darf den Nachweis einer Person sehen?

    Die Person selbst, die Buchhaltung, ein System-Admin und jeder Admin
    einer Gruppe, in der die Person Mitglied ist.
    """
    if viewer.pk == person.pk or viewer.sees_all_groups:
        return True
    return bool(set(viewer.admin_group_ids()) & set(person.member_group_ids()))


def visible_groups(viewer, person) -> list[Group] | None:
    """Die Gruppen, deren Zeiten der Betrachter auf dem Blatt sehen darf.

    None bedeutet: alle. Ein Gruppen-Admin sieht nur seine eigenen Gruppen;
    gehört die Person daneben noch einer anderen an, bleiben deren Zeiten
    außen vor.
    """
    if viewer.pk == person.pk or viewer.sees_all_groups:
        return None
    shared = set(viewer.admin_group_ids()) & set(person.member_group_ids())
    return sorted(Group.objects.filter(pk__in=shared), key=lambda group: group.name)


def _entries(user, first_day: date, last_day: date, groups: list[Group] | None):
    queryset = TimeEntry.objects.filter(user=user, end__isnull=False)
    if groups is not None:
        queryset = queryset.filter(group__in=groups)
    return (
        entries_in_range(queryset, first_day, last_day)
        .select_related("group", "activity")
        .prefetch_related(Prefetch("breaks", queryset=BreakEntry.objects.order_by("start")))
    )


def _groups_of(parts, user) -> list[Group]:
    """Die Gruppen, um die es auf dem Blatt geht.

    Ohne Zeiten im Zeitraum bleiben die Mitgliedschaften übrig; sonst stünde
    auf einem leeren Blatt keine Gruppe, und der Abschluss wäre nicht zu
    beurteilen.
    """
    groups = {part.entry.group_id: part.entry.group for part in parts}
    if not groups:
        groups = {group.pk: group for group in Group.objects.filter(pk__in=user.member_group_ids())}
    return sorted(groups.values(), key=lambda group: group.name)


def build(
    user,
    first_day: date,
    last_day: date,
    groups: list[Group] | None = None,
    *,
    closed: bool | None = None,
) -> Timesheet:
    """Baut den Nachweis einer Person für einen Zeitraum.

    Mit `groups` zählen nur die Zeiten dieser Gruppen. So bleibt das Blatt aus
    der Gruppenansicht bei seiner Gruppe, und ein Gruppen-Admin sieht auch bei
    einer Person mit mehreren Mitgliedschaften nur seine eigenen Zeiten.
    """
    parts = parts_in_range(list(_entries(user, first_day, last_day, groups)), first_day, last_day)
    by_day: dict[date, list[DayPart]] = {}
    for part in parts:
        by_day.setdefault(part.day, []).append(part)

    rows = []
    day = first_day
    while day <= last_day:
        rows.append(DayRow(day=day, parts=by_day.get(day, [])))
        day += timedelta(days=1)

    shown = list(groups) if groups is not None else _groups_of(parts, user)
    if closed is None:
        # Unterschrieben wird nur, was feststeht: jede beteiligte Gruppe muss
        # den ganzen Zeitraum abgeschlossen haben.
        closed = bool(shown) and all(
            closing.range_closed(group, first_day, last_day) for group in shown
        )

    return Timesheet(
        user=user,
        first_day=first_day,
        last_day=last_day,
        rows=rows,
        groups=shown,
        closed=closed,
        open_entries=_open_count(user, first_day, last_day, groups),
    )


def _open_count(user, first_day: date, last_day: date, groups: list[Group] | None) -> int:
    """Laufende Einträge im Zeitraum. Sie zählen auf dem Blatt nicht mit."""
    queryset = TimeEntry.objects.filter(user=user, end__isnull=True)
    if groups is not None:
        queryset = queryset.filter(group__in=groups)
    return entries_in_range(queryset, first_day, last_day).count()


def build_for_group(group: Group, first_day: date, last_day: date) -> list[Timesheet]:
    """Ein Blatt je Mitglied der Gruppe, alphabetisch."""
    members = (
        get_user_model()
        .objects.filter(group_memberships__group=group, is_active=True)
        .distinct()
        .order_by("last_name", "first_name", "email")
    )
    # Der Abschluss gilt für die ganze Gruppe; einmal nachsehen reicht.
    closed = closing.range_closed(group, first_day, last_day)
    return [build(member, first_day, last_day, [group], closed=closed) for member in members]
