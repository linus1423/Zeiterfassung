"""Die drei Erinnerungen aus Issue 34.

Jede läuft für sich, jede ist unschädlich, wenn es nichts zu erinnern gibt,
und jede räumt ihre eigenen Hinweise wieder ab, sobald der Anlass weg ist.
Aufgerufen werden sie vom Dienst `scheduler`, siehe README.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.corrections.models import CorrectionRequest
from apps.groups import closing
from apps.groups.models import Group
from apps.groups.periods import Period, period_for
from apps.tracking.daysplit import entries_in_range
from apps.tracking.models import TimeEntry
from apps.tracking.utils import day_bounds

from . import notifications, services
from .models import Reminder


@dataclass(frozen=True)
class Result:
    """Was ein Lauf bewirkt hat, für die Ausgabe des Kommandos."""

    created: int = 0
    resolved: int = 0
    mailed: int = 0


def _hours(value: float) -> str:
    """Eine Dauer in ganzen und halben Stunden, wie man sie sagt."""
    total = round(value * 2) / 2
    return f"{total:.1f}".replace(".0", "").replace(".", ",")


def _deliver(reminder: Reminder | None, subject: str, template: str, context: dict) -> int:
    if reminder is None:
        return 0
    return 1 if notifications.deliver(reminder, subject, template, context) else 0


# --- 1. Vergessenes Ausstempeln --------------------------------------------


def remind_open_entries(hours: int | None = None) -> Result:
    """Erinnert, wer deutlich länger eingestempelt ist als üblich.

    Der Hinweis kommt vor `close_stale_entries`: dann korrigiert die Person
    selbst, statt hinterher einen Antrag stellen zu müssen.
    """
    hours = hours if hours is not None else settings.OPEN_ENTRY_REMINDER_HOURS
    now = timezone.now()
    cutoff = now - timedelta(hours=hours)
    max_hours = settings.MAX_OPEN_ENTRY_HOURS
    url = reverse("tracking:clock")

    created = mailed = 0
    open_entries = list(TimeEntry.objects.open().select_related("user", "group").order_by("start"))
    for entry in open_entries:
        if entry.start > cutoff:
            continue
        since = timezone.localtime(entry.start)
        deadline = timezone.localtime(entry.start + timedelta(hours=max_hours))
        running = (now - entry.start).total_seconds() / 3600
        reminder = services.raise_reminder(
            Reminder.Kind.OPEN_ENTRY,
            entry.user,
            services.entry_key(entry.pk),
            message=(
                f"Du bist seit {since:%d.%m.%Y %H:%M} Uhr eingestempelt "
                f"({_hours(running)} Stunden, Gruppe {entry.group.name}). "
                f"Ohne Ausstempeln beendet das System den Eintrag am "
                f"{deadline:%d.%m.%Y um %H:%M} Uhr und markiert ihn als unvollständig."
            ),
            url=url,
            group=entry.group,
        )
        if reminder is not None:
            created += 1
            mailed += _deliver(
                reminder,
                "Zeiterfassung: Ausstempeln nicht vergessen",
                "reminders/mail/offener_eintrag.txt",
                {"entry": entry, "deadline": deadline},
            )

    resolved = services.resolve_except(
        Reminder.Kind.OPEN_ENTRY, [services.entry_key(entry.pk) for entry in open_entries]
    )
    return Result(created=created, resolved=resolved, mailed=mailed)


# --- 2. Liegengebliebene Anträge -------------------------------------------


def remind_pending_corrections(days: int | None = None) -> Result:
    """Erinnert die Admins an Anträge, die länger als die Frist offen liegen."""
    days = days if days is not None else settings.PENDING_CORRECTION_REMINDER_DAYS
    cutoff = timezone.now() - timedelta(days=days)
    url = reverse("corrections:inbox")

    created = mailed = 0
    pending = list(
        CorrectionRequest.objects.filter(status=CorrectionRequest.Status.PENDING)
        .select_related("group", "requested_by")
        .order_by("created_at")
    )
    for correction in pending:
        if correction.created_at > cutoff:
            continue
        waiting = (timezone.now() - correction.created_at).days
        for membership in correction.group.admins():
            # Über den eigenen Antrag entscheidet man nicht selbst.
            if membership.user_id == correction.requested_by_id:
                continue
            reminder = services.raise_reminder(
                Reminder.Kind.PENDING_CORRECTION,
                membership.user,
                services.correction_key(correction.pk),
                message=(
                    f"Der Korrekturantrag von {correction.requested_by.full_name} "
                    f"in der Gruppe {correction.group.name} liegt seit "
                    f"{waiting} Tagen offen."
                ),
                url=url,
                group=correction.group,
            )
            if reminder is not None:
                created += 1
                mailed += _deliver(
                    reminder,
                    f"Zeiterfassung: Korrekturantrag liegt seit {waiting} Tagen offen",
                    "reminders/mail/offener_antrag.txt",
                    {"correction": correction, "waiting": waiting},
                )

    resolved = services.resolve_except(
        Reminder.Kind.PENDING_CORRECTION,
        [services.correction_key(correction.pk) for correction in pending],
    )
    return Result(created=created, resolved=resolved, mailed=mailed)


# --- 3. Vor dem Abschluss ---------------------------------------------------


def _pending_in_period(group: Group, period: Period) -> int:
    """Offene Anträge, die eine Zeit in diesem Zeitraum betreffen."""
    first, _ = day_bounds(period.start)
    _, last = day_bounds(period.end)
    return (
        CorrectionRequest.objects.filter(group=group, status=CorrectionRequest.Status.PENDING)
        .filter(
            Q(proposed_start__gte=first, proposed_start__lt=last)
            | Q(time_entry__start__gte=first, time_entry__start__lt=last)
        )
        .distinct()
        .count()
    )


def _incomplete_in_period(group: Group, period: Period) -> int:
    return entries_in_range(
        TimeEntry.objects.filter(group=group, is_incomplete=True), period.start, period.end
    ).count()


def remind_period_closing(days: int | None = None, today: date | None = None) -> Result:
    """Erinnert die Admins an einen abgelaufenen, noch offenen Zeitraum."""
    days = days if days is not None else settings.PERIOD_CLOSING_REMINDER_DAYS
    today = today or timezone.localdate()
    created = mailed = 0
    for group in Group.objects.filter(is_active=True):
        period = group.current_period(today).previous()
        if today < period.end + timedelta(days=days + 1):
            # Der Zeitraum ist noch keine volle Frist her.
            continue
        if closing.lock_in_range(group, period.start, period.end) is not None:
            continue

        open_requests = _pending_in_period(group, period)
        incomplete = _incomplete_in_period(group, period)
        message = (
            f"Der Zeitraum {period.label} der Gruppe {group.name} ist abgelaufen "
            f"und noch nicht abgeschlossen. Offene Anträge: {open_requests}, "
            f"unvollständige Einträge: {incomplete}."
        )
        for membership in group.admins():
            reminder = services.raise_reminder(
                Reminder.Kind.PERIOD_CLOSING,
                membership.user,
                services.period_key(group.pk, period.start),
                message=message,
                url=reverse("groups:periods", args=[group.pk]),
                group=group,
            )
            if reminder is not None:
                created += 1
                mailed += _deliver(
                    reminder,
                    f"Zeiterfassung: {period.label} noch nicht abgeschlossen",
                    "reminders/mail/offener_zeitraum.txt",
                    {
                        "group": group,
                        "period": period,
                        "open_requests": open_requests,
                        "incomplete": incomplete,
                    },
                )

    return Result(created=created, resolved=_resolve_closed_periods(), mailed=mailed)


def _resolve_closed_periods() -> int:
    """Schließt Hinweise zu Zeiträumen, die inzwischen abgeschlossen sind.

    Der Abschluss selbst räumt seinen Hinweis schon weg; dieser Lauf fängt
    ab, was daneben passiert ist, etwa ein Abschluss über die Datenbank
    oder eine gelöschte Gruppe.
    """
    resolved = 0
    for reminder in (
        Reminder.objects.unresolved()
        .filter(kind=Reminder.Kind.PERIOD_CLOSING)
        .select_related("group")
    ):
        start = period_start_from_key(reminder.subject_key)
        if reminder.group is None or start is None:
            reminder.resolve()
            resolved += 1
            continue
        period = period_for(start, reminder.group.month_start_day)
        if closing.lock_in_range(reminder.group, period.start, period.end) is not None:
            reminder.resolve()
            resolved += 1
    return resolved


def period_start_from_key(subject_key: str) -> date | None:
    """Liest den Beginn des Zeitraums aus "period:<gruppe>:<datum>"."""
    try:
        return date.fromisoformat(subject_key.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
