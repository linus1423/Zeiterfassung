"""Die drei Erinnerungen aus Issue 34.

Jede läuft für sich, jede ist unschädlich, wenn es nichts zu erinnern gibt,
und jede räumt ihre eigenen Hinweise wieder ab, sobald der Anlass weg ist.
Aufgerufen werden sie vom Dienst `scheduler`, siehe README.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.corrections.models import CorrectionRequest
from apps.groups import closing
from apps.groups.models import Group, GroupMembership
from apps.groups.periods import Period, period_for
from apps.tracking.daysplit import entries_in_range
from apps.tracking.models import TimeEntry
from apps.tracking.utils import day_bounds

from . import notifications, services
from .models import Reminder

logger = logging.getLogger(__name__)


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


def escalation_deadline(days: int, escalation_days: int) -> int:
    """Die zweite Frist, die nie unter der ersten liegen darf (Issue 58).

    Eine Eskalation vor der ersten Erinnerung wäre sinnlos: die System-Admins
    erführen von einem Antrag, den die Gruppe selbst noch gar nicht angemahnt
    bekommen hat. Statt stillschweigend danach zu handeln, meldet der Lauf die
    Fehlkonfiguration und rückt die zweite Frist auf die erste.
    """
    if escalation_days < days:
        logger.warning(
            "PENDING_CORRECTION_ESCALATION_DAYS (%s) liegt unter "
            "PENDING_CORRECTION_REMINDER_DAYS (%s). Es gilt die erste Frist von %s Tagen.",
            escalation_days,
            days,
            days,
        )
        return days
    return escalation_days


def _active_admins(group_ids) -> dict[int, list[GroupMembership]]:
    """Die aktiven Admins je Gruppe, in einer Abfrage statt einer je Antrag."""
    admins: dict[int, list[GroupMembership]] = defaultdict(list)
    memberships = (
        GroupMembership.objects.filter(
            group_id__in=list(group_ids),
            role=GroupMembership.Role.ADMIN,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("user__last_name", "user__first_name")
    )
    for membership in memberships:
        admins[membership.group_id].append(membership)
    return admins


def _deciders(correction: CorrectionRequest, admins: list[GroupMembership]) -> list:
    """Wer in der Gruppe über diesen Antrag entscheiden könnte.

    Über den eigenen Antrag entscheidet man nicht selbst, siehe
    `apps.corrections.services.may_decide`. Bleibt niemand übrig, hilft nur
    noch ein System-Admin.
    """
    return [membership for membership in admins if membership.user_id != correction.requested_by_id]


def _waiting_days(now: datetime, correction: CorrectionRequest) -> int:
    return (now - correction.created_at).days


def remind_pending_corrections(
    days: int | None = None, escalation_days: int | None = None
) -> Result:
    """Erinnert an Anträge, die länger als die Frist offen liegen.

    Nach der ersten Frist gehen die Hinweise an die Admins der Gruppe, nach
    der zweiten, längeren zusätzlich an die System-Admins (Issue 58). Sonst
    liegt eine Gruppe, deren einziger Admin im Urlaub ist, bis zu seiner
    Rückkehr still.
    """
    days = days if days is not None else settings.PENDING_CORRECTION_REMINDER_DAYS
    escalation_days = (
        escalation_days
        if escalation_days is not None
        else settings.PENDING_CORRECTION_ESCALATION_DAYS
    )
    escalation_days = escalation_deadline(days, escalation_days)

    now = timezone.now()
    cutoff = now - timedelta(days=days)
    url = reverse("corrections:inbox")

    created = mailed = 0
    pending = list(
        CorrectionRequest.objects.filter(status=CorrectionRequest.Status.PENDING)
        .select_related("group", "requested_by")
        .order_by("created_at")
    )
    overdue = [correction for correction in pending if correction.created_at <= cutoff]
    admins = _active_admins({correction.group_id for correction in overdue})

    for correction in overdue:
        waiting = _waiting_days(now, correction)
        for membership in _deciders(correction, admins[correction.group_id]):
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

    escalated = _escalate_to_system_admins(overdue, admins, now, escalation_days, url)

    resolved = services.resolve_except(
        Reminder.Kind.PENDING_CORRECTION,
        [services.correction_key(correction.pk) for correction in pending],
    )
    return Result(
        created=created + escalated.created,
        resolved=resolved + escalated.resolved,
        mailed=mailed + escalated.mailed,
    )


def _stuck_reason(correction: CorrectionRequest, admins: list[GroupMembership], days: int) -> str:
    """Warum dieser Antrag in seiner Gruppe nicht entschieden wird."""
    if not admins:
        return "Die Gruppe hat zurzeit keinen aktiven Admin."
    if not _deciders(correction, admins):
        return (
            "Der Antragsteller ist der einzige aktive Admin der Gruppe und darf "
            "über den eigenen Antrag nicht entscheiden."
        )
    return f"Die Frist von {days} Tagen ist überschritten."


def _escalation_message(
    corrections: list[CorrectionRequest],
    admins: list[GroupMembership],
    now: datetime,
    days: int,
) -> str:
    """Der Text der Eskalation: Gruppe, Anzahl, ältester Antrag und Grund."""
    oldest = corrections[0]
    since = timezone.localtime(oldest.created_at)
    count = len(corrections)
    zahl = "1 Korrekturantrag wartet" if count == 1 else f"{count} Korrekturanträge warten"
    return (
        f"In der Gruppe {oldest.group.name} {zahl} auf eine Entscheidung, die dort "
        f"nicht fällt. Der älteste stammt von {oldest.requested_by.full_name} und "
        f"liegt seit {_waiting_days(now, oldest)} Tagen offen (seit {since:%d.%m.%Y}). "
        f"{_stuck_reason(oldest, admins, days)}"
    )


def _escalate_to_system_admins(
    overdue: list[CorrectionRequest],
    admins: dict[int, list[GroupMembership]],
    now: datetime,
    escalation_days: int,
    url: str,
) -> Result:
    """Meldet den System-Admins, was in einer Gruppe niemand entscheidet (Issue 58).

    Eskaliert wird ein Antrag, der die zweite Frist überschritten hat, und
    ohne weitere Wartezeit einer, über den in der Gruppe niemand entscheiden
    darf: auf die zweite Frist zu warten hieße dort, auf jemanden zu warten,
    den es nicht gibt.

    Je Gruppe entsteht ein Hinweis, und zwar am ältesten betroffenen Antrag.
    Damit eskaliert derselbe Antrag höchstens einmal. Ist er entschieden,
    verfällt der Hinweis mit ihm; liegt dann noch etwas, meldet der nächste
    Lauf den neuen ältesten mit aktuellen Zahlen.
    """
    escalation_cutoff = now - timedelta(days=escalation_days)
    stuck: dict[int, list[CorrectionRequest]] = defaultdict(list)
    for correction in overdue:
        if correction.created_at <= escalation_cutoff or not _deciders(
            correction, admins[correction.group_id]
        ):
            stuck[correction.group_id].append(correction)

    if not stuck:
        return Result(resolved=services.resolve_except(Reminder.Kind.CORRECTION_ESCALATION, []))

    system_admins = list(get_user_model().objects.filter(is_superuser=True, is_active=True))
    created = mailed = 0
    keys = []
    for corrections in stuck.values():
        oldest = corrections[0]
        keys.append(services.escalation_key(oldest.pk))
        group_admins = admins[oldest.group_id]
        message = _escalation_message(corrections, group_admins, now, escalation_days)
        # Wer den Antrag als Admin der Gruppe ohnehin schon angemahnt bekommen
        # hat, braucht dieselbe Sache nicht ein zweites Mal.
        decider_ids = {membership.user_id for membership in _deciders(oldest, group_admins)}
        for recipient in system_admins:
            if recipient.pk in decider_ids:
                continue
            reminder = services.raise_reminder(
                Reminder.Kind.CORRECTION_ESCALATION,
                recipient,
                services.escalation_key(oldest.pk),
                message=message,
                url=url,
                group=oldest.group,
            )
            if reminder is not None:
                created += 1
                mailed += _deliver(
                    reminder,
                    f"Zeiterfassung: Anträge der Gruppe {oldest.group.name} bleiben liegen",
                    "reminders/mail/eskalation_antraege.txt",
                    {
                        "correction": oldest,
                        "group": oldest.group,
                        "count": len(corrections),
                        "waiting": _waiting_days(now, oldest),
                        "reason": _stuck_reason(oldest, group_admins, escalation_days),
                    },
                )

    resolved = services.resolve_except(Reminder.Kind.CORRECTION_ESCALATION, keys)
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
