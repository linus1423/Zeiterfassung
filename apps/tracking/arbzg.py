"""Prüfung des Arbeitszeitgesetzes über den ganzen Tag (Issue 49).

Geprüft wird nie ein einzelner Zeiteintrag, sondern der Tag als Ganzes: zwei
Einträge von je vier Stunden sind zusammen acht Stunden Arbeitszeit und
brauchen deshalb dieselbe Pause wie ein Eintrag über acht Stunden.

Drei Regeln:

* § 4 ArbZG — Pause: ab sechs Stunden Arbeitszeit 30 Minuten, ab neun
  Stunden 45 Minuten.
* § 3 ArbZG — Höchstarbeitszeit: zehn Stunden am Tag.
* § 5 ArbZG — Ruhezeit: elf Stunden zwischen Feierabend und dem nächsten
  Beginn.

Es wird ausschließlich gewarnt und niemals etwas abgezogen oder gekappt: was
gestempelt wurde, bleibt stehen. Ob die Zeit zulässig war, entscheiden
Menschen, nicht dieses Programm.

Die Tagessummen kommen aus `daysplit`, damit eine Nachtschicht anteilig auf
beide Tage zählt und ein Tag mit Zeitumstellung 23 oder 25 Stunden hat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from .daysplit import entries_in_range, parts_in_range
from .models import BreakEntry, elapsed
from .templatetags.zeit import dauer

# --- Grenzwerte, an einer Stelle -------------------------------------------
# Absichtlich Konstanten und keine Einstellungen: das sind die Werte des
# Gesetzes und keine Frage des Geschmacks. Abschalten lassen sich die Hinweise
# über STATUTORY_BREAK_WARNINGS und STATUTORY_LIMIT_WARNINGS.
WORK_BEFORE_SHORT_BREAK = timedelta(hours=6)
SHORT_BREAK = timedelta(minutes=30)
WORK_BEFORE_LONG_BREAK = timedelta(hours=9)
LONG_BREAK = timedelta(minutes=45)
MAX_DAILY_WORK = timedelta(hours=10)
MIN_REST = timedelta(hours=11)


class Rule:
    """Kennungen der Regeln. Bewusst ASCII, die Beschriftung steht daneben."""

    BREAK = "break"
    DAILY_MAX = "daily_max"
    REST = "rest"


RULE_LABELS = {
    Rule.BREAK: "Pause (§ 4 ArbZG)",
    Rule.DAILY_MAX: "Höchstarbeitszeit (§ 3 ArbZG)",
    Rule.REST: "Ruhezeit (§ 5 ArbZG)",
}


@dataclass(frozen=True)
class DaySummary:
    """Was eine Person an einem Tag gearbeitet und pausiert hat."""

    user: object
    day: date
    work: timedelta
    breaks: timedelta


@dataclass(frozen=True)
class Violation:
    """Eine verletzte Regel an einem Tag."""

    user: object
    day: date
    rule: str
    value: timedelta
    limit: timedelta
    value_text: str
    message: str

    @property
    def rule_label(self) -> str:
        return RULE_LABELS.get(self.rule, self.rule)


def _day_text(day: date) -> str:
    return f"{day:%d.%m.%Y}"


def _local_end_day(moment: datetime) -> date:
    """Der Tag, zu dem ein Ende gehört.

    Eine Mikrosekunde vor dem Zeitpunkt, damit ein Eintrag, der um Punkt Mitternacht
    endet, noch zum Vortag zählt — genauso wie in `TimeEntry.spans_days`.
    """
    return timezone.localtime(moment - timedelta(microseconds=1)).date()


def _break_violation(summary: DaySummary) -> Violation | None:
    """§ 4 ArbZG, gerechnet auf die Arbeitszeit des ganzen Tages."""
    if not settings.STATUTORY_BREAK_WARNINGS:
        return None

    work, paused = summary.work, summary.breaks
    if work > WORK_BEFORE_LONG_BREAK and paused < LONG_BREAK:
        required = LONG_BREAK
        rule_text = "Ab neun Stunden Arbeitszeit sind 45 Minuten Pause vorgeschrieben."
    elif work > WORK_BEFORE_SHORT_BREAK and paused < SHORT_BREAK:
        required = SHORT_BREAK
        rule_text = "Ab sechs Stunden Arbeitszeit sind 30 Minuten Pause vorgeschrieben."
    else:
        return None

    return Violation(
        user=summary.user,
        day=summary.day,
        rule=Rule.BREAK,
        value=paused,
        limit=required,
        value_text=f"{dauer(paused)} h Pause bei {dauer(work)} h Arbeit",
        message=(
            f"{_day_text(summary.day)}: {rule_text} "
            f"Erfasst sind {dauer(work)} h Arbeitszeit und {dauer(paused)} h Pause."
        ),
    )


def _daily_max_violation(summary: DaySummary) -> Violation | None:
    """§ 3 ArbZG: zehn Stunden am Tag."""
    if not settings.STATUTORY_LIMIT_WARNINGS or summary.work <= MAX_DAILY_WORK:
        return None

    return Violation(
        user=summary.user,
        day=summary.day,
        rule=Rule.DAILY_MAX,
        value=summary.work,
        limit=MAX_DAILY_WORK,
        value_text=f"{dauer(summary.work)} h Arbeitszeit",
        message=(
            f"{_day_text(summary.day)}: An einem Tag sind höchstens zehn Stunden "
            f"Arbeitszeit zulässig. Erfasst sind {dauer(summary.work)} h."
        ),
    )


def _rest_violations(user, entries) -> list[Violation]:
    """§ 5 ArbZG: elf Stunden Ruhezeit zwischen zwei Arbeitstagen.

    Gemessen wird die Lücke zwischen dem Ende eines Eintrags und dem Beginn
    des nächsten, und zwar nur dann, wenn die beiden auf verschiedene Tage
    fallen. Wer sich mittags aus- und eine Stunde später wieder einstempelt,
    hat damit keinen Feierabend gemacht, sondern seinen Arbeitstag
    unterbrochen; das wäre sonst jeden Tag eine Meldung.
    """
    if not settings.STATUTORY_LIMIT_WARNINGS:
        return []

    found: list[Violation] = []
    previous_end: datetime | None = None
    for entry in sorted(entries, key=lambda item: item.start):
        if previous_end is not None:
            gap = elapsed(previous_end, entry.start)
            starts_on = timezone.localtime(entry.start).date()
            if timedelta() < gap < MIN_REST and _local_end_day(previous_end) != starts_on:
                found.append(
                    Violation(
                        user=user,
                        day=starts_on,
                        rule=Rule.REST,
                        value=gap,
                        limit=MIN_REST,
                        value_text=f"{dauer(gap)} h Ruhezeit",
                        message=(
                            f"{_day_text(starts_on)}: Zwischen zwei Arbeitstagen sind elf "
                            f"Stunden Ruhezeit vorgeschrieben. Zwischen Feierabend und "
                            f"Beginn liegen nur {dauer(gap)} h."
                        ),
                    )
                )
        # Einträge derselben Person überschneiden sich nicht, der zuletzt
        # begonnene ist also auch der zuletzt beendete. Ein laufender Eintrag
        # hat noch keinen Feierabend und beendet die Kette.
        previous_end = entry.end
    return found


def day_summaries(entries, first_day: date, last_day: date) -> list[DaySummary]:
    """Arbeits- und Pausenzeit je Person und Tag, aus den Tagesanteilen."""
    totals: dict[tuple[int, date], list] = {}
    for part in parts_in_range(entries, first_day, last_day):
        key = (part.entry.user_id, part.day)
        row = totals.get(key)
        if row is None:
            totals[key] = [part.entry.user, timedelta(), timedelta()]
            row = totals[key]
        row[1] += part.work
        row[2] += part.breaks

    return [
        DaySummary(user=user, day=day, work=work, breaks=paused)
        for (_, day), (user, work, paused) in totals.items()
    ]


def violations(entries, first_day: date, last_day: date) -> list[Violation]:
    """Alle Regelverstöße im Zeitraum, nach Tag und Person sortiert.

    `entries` darf und soll auch den Tag vor dem Zeitraum enthalten: die
    Ruhezeit des ersten Tages beginnt am Abend davor. Gemeldet wird trotzdem
    nur, was im Zeitraum liegt.
    """
    found: list[Violation] = []
    for summary in day_summaries(entries, first_day, last_day):
        for violation in (_break_violation(summary), _daily_max_violation(summary)):
            if violation is not None:
                found.append(violation)

    by_user: dict[int, list] = {}
    users: dict[int, object] = {}
    for entry in entries:
        by_user.setdefault(entry.user_id, []).append(entry)
        users.setdefault(entry.user_id, entry.user)
    for user_id, user_entries in by_user.items():
        found.extend(
            violation
            for violation in _rest_violations(users[user_id], user_entries)
            if first_day <= violation.day <= last_day
        )

    found.sort(key=lambda item: (item.day, str(item.user), item.rule))
    return found


def entries_for_check(queryset, first_day: date, last_day: date):
    """Die Einträge, die für die Prüfung gebraucht werden, in einer Abfrage.

    Einen Tag mehr am Anfang wegen der Ruhezeit, Person und Pausen gleich
    mitgeladen: sonst wächst die Prüfung mit jeder Person um Abfragen.
    """
    return (
        entries_in_range(queryset, first_day - timedelta(days=1), last_day)
        .select_related("user")
        .prefetch_related(Prefetch("breaks", queryset=BreakEntry.objects.order_by("start")))
        .order_by("start")
    )


def check(queryset, first_day: date, last_day: date) -> list[Violation]:
    """Prüft einen Zeitraum und gibt die Verstöße zurück."""
    entries = list(entries_for_check(queryset, first_day, last_day))
    return violations(entries, first_day, last_day)
