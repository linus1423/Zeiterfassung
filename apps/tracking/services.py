"""Stempel-Logik.

Der Zustand eines Nutzers ist immer genau einer von dreien: ausgestempelt,
arbeitet, in Pause. Alle Uebergaenge laufen ueber diese Funktionen, damit die
Regeln nicht in den Views verstreut liegen. Die Uhrzeit kommt immer vom
Server, nie aus dem Browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditLog, log

from .models import BreakEntry, TimeEntry


class ClockError(Exception):
    """Ein Stempelvorgang passt nicht zum aktuellen Zustand."""


@dataclass(frozen=True)
class ClockState:
    entry: TimeEntry | None
    open_break: BreakEntry | None

    @property
    def is_clocked_in(self) -> bool:
        return self.entry is not None

    @property
    def is_on_break(self) -> bool:
        return self.open_break is not None

    @property
    def label(self) -> str:
        if not self.is_clocked_in:
            return "ausgestempelt"
        return "in Pause" if self.is_on_break else "arbeitet"


def get_state(user) -> ClockState:
    entry = (
        TimeEntry.objects.open()
        .filter(user=user)
        .select_related("group", "activity")
        .prefetch_related("breaks")
        .first()
    )
    open_break = entry.open_break if entry else None
    return ClockState(entry=entry, open_break=open_break)


def _open_entry_for_update(user) -> TimeEntry:
    entry = TimeEntry.objects.select_for_update().filter(user=user, end__isnull=True).first()
    if entry is None:
        raise ClockError("Du bist gerade nicht eingestempelt.")
    return entry


@transaction.atomic
def clock_in(user, group, activity=None, *, note: str = "") -> TimeEntry:
    """Startet einen Zeiteintrag."""
    if not user.is_group_member(group):
        raise ClockError("Du bist kein Mitglied dieser Gruppe.")
    if activity is not None:
        if activity.group_id != group.pk:
            raise ClockError("Die Taetigkeit gehoert zu einer anderen Gruppe.")
        if not activity.is_active:
            raise ClockError("Diese Taetigkeit ist nicht mehr waehlbar.")

    now = timezone.now()
    if TimeEntry.objects.overlapping(user, now).exists():
        raise ClockError("Du bist bereits eingestempelt.")

    try:
        entry = TimeEntry.objects.create(
            user=user,
            group=group,
            activity=activity,
            start=now,
            note=note,
            source=TimeEntry.Source.CLOCK,
        )
    except IntegrityError as exc:
        # Der Datenbank-Constraint faengt zwei gleichzeitige Klicks ab.
        raise ClockError("Du bist bereits eingestempelt.") from exc

    log(AuditLog.Action.CLOCK_IN, actor=user, target=entry, group=group, subject=user)
    return entry


@transaction.atomic
def start_break(user) -> BreakEntry:
    entry = _open_entry_for_update(user)
    if entry.is_on_break:
        raise ClockError("Du bist bereits in einer Pause.")

    pause = BreakEntry.objects.create(time_entry=entry, start=timezone.now())
    log(AuditLog.Action.BREAK_START, actor=user, target=entry, group=entry.group, subject=user)
    return pause


@transaction.atomic
def end_break(user) -> BreakEntry:
    entry = _open_entry_for_update(user)
    pause = entry.breaks.select_for_update().filter(end__isnull=True).first()
    if pause is None:
        raise ClockError("Es laeuft gerade keine Pause.")

    pause.end = timezone.now()
    if pause.end <= pause.start:
        pause.end = pause.start + timedelta(seconds=1)
    pause.save(update_fields=["end"])
    log(AuditLog.Action.BREAK_END, actor=user, target=entry, group=entry.group, subject=user)
    return pause


@transaction.atomic
def clock_out(user) -> TimeEntry:
    """Beendet den laufenden Eintrag und eine eventuell laufende Pause."""
    entry = _open_entry_for_update(user)
    now = timezone.now()

    pause = entry.breaks.select_for_update().filter(end__isnull=True).first()
    if pause is not None:
        pause.end = max(now, pause.start + timedelta(seconds=1))
        pause.save(update_fields=["end"])

    entry.end = max(now, entry.start + timedelta(seconds=1))
    entry.save(update_fields=["end", "updated_at"])
    log(AuditLog.Action.CLOCK_OUT, actor=user, target=entry, group=entry.group, subject=user)
    return entry


def close_stale_entries(max_hours: int | None = None) -> int:
    """Beendet vergessene Eintraege und markiert sie als unvollstaendig.

    Wird vom Management-Kommando `close_stale_entries` aufgerufen.
    """
    max_hours = max_hours if max_hours is not None else settings.MAX_OPEN_ENTRY_HOURS
    cutoff = timezone.now() - timedelta(hours=max_hours)
    closed = 0

    for entry in TimeEntry.objects.open().filter(start__lt=cutoff).iterator():
        with transaction.atomic():
            locked = TimeEntry.objects.select_for_update().filter(pk=entry.pk).first()
            if locked is None or locked.end is not None:
                continue
            deadline = locked.start + timedelta(hours=max_hours)
            open_break = locked.breaks.filter(end__isnull=True).first()
            if open_break is not None:
                # Eine Pause, die spaeter als die Hoechstdauer begonnen hat,
                # verschiebt das Ende, damit sie im Eintrag liegen bleibt.
                deadline = max(deadline, open_break.start + timedelta(seconds=1))
                open_break.end = deadline
                open_break.save(update_fields=["end"])
            locked.end = deadline
            locked.is_incomplete = True
            locked.save(update_fields=["end", "is_incomplete", "updated_at"])
            log(
                AuditLog.Action.AUTO_CLOSE,
                target=locked,
                group=locked.group,
                subject=locked.user,
                note=f"Automatisch beendet nach {max_hours} Stunden.",
            )
            closed += 1

    return closed


def statutory_break_warning(worked: timedelta, paused: timedelta) -> str:
    """Hinweis auf gesetzliche Pausen. Es wird nur gewarnt, nie abgezogen."""
    if not settings.STATUTORY_BREAK_WARNINGS:
        return ""
    hours = worked.total_seconds() / 3600
    minutes_paused = paused.total_seconds() / 60
    if hours > 9 and minutes_paused < 45:
        return "Ab neun Stunden Arbeitszeit sind 45 Minuten Pause vorgeschrieben."
    if hours > 6 and minutes_paused < 30:
        return "Ab sechs Stunden Arbeitszeit sind 30 Minuten Pause vorgeschrieben."
    return ""
