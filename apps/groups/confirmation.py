"""Mitarbeitende bestätigen ihren Abrechnungszeitraum (Issue 50).

Vor dem Abschluss durch den Gruppen-Admin sieht jedes Mitglied seine eigenen
Tage des Zeitraums und bestätigt sie. Die Bestätigung hält den Abschluss
nicht auf, sie sagt dem Admin nur, wer noch nicht hingesehen hat.

Wann eine Bestätigung verfällt:
Bestätigt wird der Stand der Zeiten, den die Person gesehen hat, und nicht
der Zeitraum als solcher. Dieser Stand sind zwei Zahlen (siehe
`Fingerprint`): wie viele eigene Zeiteinträge den Zeitraum berühren und wann
davon zuletzt einer gespeichert wurde. Stimmen später beide noch, gilt die
Bestätigung; weicht eine ab, ist sie verfallen. Damit verfällt sie von
allein, ohne dass eine andere Stelle im Code daran denken muss: eine
genehmigte Korrektur und eine Änderung durch den Admin speichern den
Eintrag und heben damit `TimeEntry.updated_at`, ein Nachtrag und eine
Löschung verändern die Anzahl. Im Zweifel verfällt sie eher zu oft als zu
selten, und erneut zu bestätigen kostet einen Klick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q
from django.utils import timezone

from apps.audit.models import AuditLog, log
from apps.tracking.daysplit import entries_in_range, parts_in_range
from apps.tracking.models import BreakEntry, TimeEntry
from apps.tracking.utils import day_bounds

from . import closing
from .models import Group, PeriodConfirmation
from .periods import Period, recent_periods

# Wie viele abgelaufene Zeiträume zur Auswahl stehen. Weiter zurück liegt
# ohnehin alles abgeschlossen.
CHOOSABLE_PERIODS = 6


class ConfirmationError(Exception):
    """Der Zeitraum kann so nicht bestätigt werden."""


@dataclass(frozen=True)
class Fingerprint:
    """Der Stand der Zeiten eines Zeitraums, auf zwei Zahlen gebracht."""

    entry_count: int
    last_change_at: datetime | None

    def matches(self, confirmation: PeriodConfirmation) -> bool:
        return (
            confirmation.entry_count == self.entry_count
            and confirmation.last_change_at == self.last_change_at
        )


@dataclass(frozen=True)
class ConfirmationState:
    """Steht die Bestätigung einer Person zu einem Zeitraum noch?"""

    confirmation: PeriodConfirmation | None
    is_stale: bool

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation is not None and not self.is_stale

    @property
    def is_missing(self) -> bool:
        """Fehlt die Bestätigung, weil es keine gibt oder sie verfallen ist?"""
        return not self.is_confirmed


def may_confirm(user, group: Group) -> bool:
    """Bestätigen darf nur, wer selbst Mitglied der Gruppe ist.

    Auch ein System-Admin oder die Buchhaltung bestätigt nichts für andere:
    bestätigt wird die eigene Arbeitszeit.
    """
    return user.is_authenticated and user.is_group_member(group)


def choosable_periods(group: Group, count: int = CHOOSABLE_PERIODS, today: date | None = None):
    """Die abgelaufenen Zeiträume der Gruppe, der jüngste zuerst.

    Der laufende Zeitraum fehlt: solange er läuft, kommen noch Zeiten dazu.
    """
    today = today or timezone.localdate()
    periods = recent_periods(group, count=count + 1, today=today)
    return [period for period in periods if period.end < today][:count]


def entries_for(user, group: Group, period: Period):
    """Die eigenen Einträge dieser Gruppe, die den Zeitraum berühren.

    Auch die, die davor beginnen und hineinlaufen: von ihnen zählt der
    Anteil im Zeitraum (Issue 32).
    """
    return entries_in_range(
        TimeEntry.objects.filter(user=user, group=group), period.start, period.end
    )


def entries_with_breaks(user, group: Group, period: Period) -> list[TimeEntry]:
    """Die eigenen Einträge des Zeitraums, fertig für die Anzeige."""
    return list(
        entries_for(user, group, period)
        .select_related("activity")
        .prefetch_related(Prefetch("breaks", queryset=BreakEntry.objects.order_by("start")))
        .order_by("start")
    )


def fingerprint(user, group: Group, period: Period) -> Fingerprint:
    """Der aktuelle Stand der eigenen Zeiten im Zeitraum, in einer Abfrage."""
    row = entries_for(user, group, period).aggregate(
        entries=Count("pk"), last_change=Max("updated_at")
    )
    return Fingerprint(entry_count=row["entries"] or 0, last_change_at=row["last_change"])


def state_for(user, group: Group, period: Period) -> ConfirmationState:
    """Die gespeicherte Bestätigung und ob sie noch zum Stand der Zeiten passt."""
    confirmation = PeriodConfirmation.objects.filter(
        user=user, group=group, period_start=period.start
    ).first()
    if confirmation is None:
        return ConfirmationState(confirmation=None, is_stale=False)
    current = fingerprint(user, group, period)
    return ConfirmationState(confirmation=confirmation, is_stale=not current.matches(confirmation))


def day_totals(entries, period: Period) -> list[tuple[date, timedelta]]:
    """Die Tagessummen des Zeitraums, aufsteigend.

    Gerechnet wird über die Tagesaufteilung, damit eine Nachtschicht mit
    ihrem Anteil in jedem Tag steht (apps/tracking/daysplit.py).
    """
    by_day: dict[date, timedelta] = {}
    for part in parts_in_range(entries, period.start, period.end):
        by_day[part.day] = by_day.get(part.day, timedelta()) + part.work
    return sorted(by_day.items())


def total_work(entries, period: Period) -> timedelta:
    """Die Gesamtsumme des Zeitraums."""
    return sum((total for _, total in day_totals(entries, period)), timedelta())


def open_request_count(user, group: Group, period: Period) -> int:
    """Offene eigene Korrekturanträge, die diesen Zeitraum berühren."""
    from apps.corrections.models import CorrectionRequest

    window_start, _ = day_bounds(period.start)
    _, window_end = day_bounds(period.end)
    return (
        CorrectionRequest.objects.filter(
            requested_by=user, group=group, status=CorrectionRequest.Status.PENDING
        )
        .filter(
            Q(proposed_start__gte=window_start, proposed_start__lt=window_end)
            | Q(time_entry__start__gte=window_start, time_entry__start__lt=window_end)
        )
        .distinct()
        .count()
    )


@transaction.atomic
def confirm(user, group: Group, period: Period) -> PeriodConfirmation:
    """Hält fest, dass die Person ihren Zeitraum geprüft hat.

    Festgehalten wird neben dem Zeitpunkt der Stand der Zeiten (siehe
    `Fingerprint`) und die Summe, die dabei auf dem Bildschirm stand.
    """
    if not may_confirm(user, group):
        raise ConfirmationError("Bestätigen kann einen Zeitraum nur ein Mitglied dieser Gruppe.")
    if period.end >= timezone.localdate():
        raise ConfirmationError("Ein Zeitraum kann erst nach seinem Ende bestätigt werden.")

    lock = closing.lock_in_range(group, period.start, period.end)
    if lock is not None:
        raise ConfirmationError(
            f"Der Zeitraum {lock.period.label} ist bereits abgeschlossen "
            "und kann nicht mehr bestätigt werden."
        )

    current = fingerprint(user, group, period)
    total = total_work(entries_with_breaks(user, group, period), period)

    confirmation, _ = PeriodConfirmation.objects.update_or_create(
        user=user,
        group=group,
        period_start=period.start,
        defaults={
            "period_end": period.end,
            "confirmed_at": timezone.now(),
            "entry_count": current.entry_count,
            "last_change_at": current.last_change_at,
            "total_minutes": max(0, int(total.total_seconds() // 60)),
        },
    )

    # Das Komma nur in der Zahl, nicht im Datum eines Zeitraums, der nicht
    # dem Kalendermonat folgt.
    hours = f"{total.total_seconds() / 3600:.2f}".replace(".", ",")
    log(
        AuditLog.Action.PERIOD_CONFIRMED,
        actor=user,
        target=confirmation,
        group=group,
        subject=user,
        note=(
            f"{period.label} bestätigt: {hours} Stunden auf {current.entry_count} Zeiteinträgen."
        ),
    )
    return confirmation


@dataclass(frozen=True)
class MissingConfirmations:
    """Wer hat einen Zeitraum noch nicht (mehr) bestätigt?"""

    period: Period
    members: int
    missing: list

    @property
    def count(self) -> int:
        return len(self.missing)

    @property
    def all_confirmed(self) -> bool:
        return self.members > 0 and not self.missing

    @property
    def names(self) -> str:
        return ", ".join(person.full_name for person in self.missing)


def _period_bounds(periods: list[Period]) -> list[tuple[Period, datetime, datetime]]:
    return [(period, *_window(period)) for period in periods]


def _window(period: Period) -> tuple[datetime, datetime]:
    window_start, _ = day_bounds(period.start)
    _, window_end = day_bounds(period.end)
    return window_start, window_end


def _fingerprints(group: Group, periods: list[Period]) -> dict[tuple[int, date], Fingerprint]:
    """Der Stand aller Mitglieder für alle Zeiträume, in einer Abfrage.

    Die Abschluss-Seite zeigt ein Dutzend Zeiträume; je Zeitraum und Person
    zu fragen wären Hunderte Abfragen. Deshalb kommen die Einträge des
    ganzen gezeigten Bereichs auf einmal und werden hier zugeordnet. Die
    Bedingung ist dieselbe wie in `entries_in_range`, damit ein Eintrag, der
    um Punkt Mitternacht endet, hier und dort gleich gezählt wird.
    """
    bounds = _period_bounds(periods)
    first = min(period.start for period in periods)
    last = max(period.end for period in periods)
    rows = entries_in_range(TimeEntry.objects.filter(group=group), first, last).values_list(
        "user_id", "start", "end", "updated_at"
    )

    collected: dict[tuple[int, date], tuple[int, datetime | None]] = {}
    for user_id, start, end, updated_at in rows:
        for period, window_start, window_end in bounds:
            if start < window_end and (end is None or end > window_start):
                key = (user_id, period.start)
                count, latest = collected.get(key, (0, None))
                collected[key] = (
                    count + 1,
                    updated_at if latest is None or updated_at > latest else latest,
                )
    return {
        key: Fingerprint(entry_count=count, last_change_at=latest)
        for key, (count, latest) in collected.items()
    }


def missing_confirmations(group: Group, periods) -> dict[date, MissingConfirmations]:
    """Je Zeitraum: wer von den aktiven Mitgliedern fehlt noch.

    Gezählt werden alle aktiven Mitglieder der Gruppe, auch die ohne eine
    einzige Zeit im Zeitraum: gerade ein leerer Zeitraum will bestätigt sein.
    """
    periods = list(periods)
    if not periods:
        return {}

    members = list(
        get_user_model()
        .objects.filter(group_memberships__group=group, is_active=True)
        .distinct()
        .order_by("last_name", "first_name", "username")
    )
    if not members:
        return {
            period.start: MissingConfirmations(period=period, members=0, missing=[])
            for period in periods
        }

    starts = [period.start for period in periods]
    confirmations = {
        (row.user_id, row.period_start): row
        for row in PeriodConfirmation.objects.filter(group=group, period_start__in=starts)
    }
    prints = _fingerprints(group, periods)
    empty = Fingerprint(entry_count=0, last_change_at=None)

    result: dict[date, MissingConfirmations] = {}
    for period in periods:
        missing = []
        for person in members:
            confirmation = confirmations.get((person.pk, period.start))
            current = prints.get((person.pk, period.start), empty)
            if confirmation is None or not current.matches(confirmation):
                missing.append(person)
        result[period.start] = MissingConfirmations(
            period=period, members=len(members), missing=missing
        )
    return result


def lock_for_period(group: Group, period: Period):
    """Der Abschluss, der diesen Zeitraum bereits sperrt, falls es einen gibt."""
    return closing.lock_in_range(group, period.start, period.end)
