"""Geplante Exporte bauen und verschicken (Issue 55).

Aufgerufen vom Kommando `send_scheduled_exports` im Dienst `scheduler`.
Für jeden aktiven Plan wird die jüngste Fälligkeit bestimmt (der Tag im
Monat, der zuletzt erreicht wurde), der Zeitraum daraus relativ zum Lauf
gerechnet, der Export gebaut und an die hinterlegten Adressen geschickt.

So wird ein Lauf höchstens einmal je Fälligkeit ausgeführt, auch wenn das
Kommando mehrfach oder gleichzeitig startet:

1. Zu jeder Fälligkeit gehört genau eine Zeile `ExportRun`; die
   Eindeutigkeitsbedingung über (schedule, due_on) lässt eine zweite gar
   nicht erst entstehen. Wer sie anlegen kann, hat den Lauf.
2. Existiert die Zeile schon, wird sie nur übernommen, wenn ein bedingtes
   UPDATE auf den unveränderten Stand (Status und Startzeit) genau eine
   Zeile trifft. Der zweite Prozess trifft dann keine mehr und geht weiter.
   Das kommt ohne Sperren aus und funktioniert auf SQLite wie auf Postgres.
3. "sent" und "blocked" werden nie wieder angefasst. "failed" wird beim
   nächsten Lauf erneut versucht, "running" erst nach STALE_AFTER -- dann
   gilt der vorige Prozess als abgestürzt.
4. Zurückliegende Fälligkeiten werden nicht nachgeholt: betrachtet wird nur
   die jüngste, und auch die nur, wenn sie nicht vor dem Anlegen des Plans
   liegt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from apps.audit.models import AuditLog, log
from apps.groups.closing import ClosedPeriods
from apps.groups.periods import period_for
from apps.groups.permissions import readable_groups
from zeiterfassung.mailing import send_mail_with_attachment

from . import services
from .access import may_schedule
from .columns import resolve
from .forms import CSV_DIALECTS
from .models import ExportRun, ExportSchedule

# Nach dieser Zeit gilt ein als "läuft" markierter Lauf als abgestürzt und
# darf von einem anderen Prozess übernommen werden.
STALE_AFTER = timedelta(minutes=30)

XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIMETYPE = "text/csv"


@dataclass(frozen=True)
class Result:
    """Was ein Lauf bewirkt hat, für die Ausgabe des Kommandos."""

    sent: int = 0
    failed: int = 0
    skipped: int = 0


class ExportTooLarge(Exception):
    """Der Export wäre zu groß für einen Mailanhang."""


def max_attachment_bytes() -> int:
    return int(getattr(settings, "EXPORT_MAX_ATTACHMENT_MB", 10)) * 1024 * 1024


def max_rows() -> int:
    return int(getattr(settings, "EXPORT_MAX_ROWS", 50000))


def emails_enabled() -> bool:
    return bool(getattr(settings, "EXPORT_EMAILS_ENABLED", False))


# --- Fälligkeit -------------------------------------------------------------


def due_date(schedule: ExportSchedule, today: date) -> date:
    """Die jüngste Fälligkeit dieses Plans, heute eingeschlossen.

    Der Tag im Monat liegt zwischen 1 und 28, den gibt es in jedem Monat.
    """
    day = max(1, min(int(schedule.day_of_month or 1), 28))
    if today.day >= day:
        return date(today.year, today.month, day)
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return date(year, month, day)


def _start_day(schedule: ExportSchedule) -> int:
    """Der Abrechnungszyklus des Plans: der seiner Gruppe, sonst der Kalendermonat."""
    return schedule.period_group.month_start_day if schedule.period_group_id else 1


def timeframe_for(schedule: ExportSchedule, due_on: date) -> tuple[date, date]:
    """Der auszuwertende Zeitraum, relativ zur Fälligkeit gerechnet."""
    current = period_for(due_on, _start_day(schedule))
    if schedule.timeframe == ExportSchedule.Timeframe.CURRENT_PERIOD:
        return current.start, current.end
    last = current.previous()
    if schedule.timeframe == ExportSchedule.Timeframe.LAST_TWELVE:
        first = last
        for _ in range(11):
            first = first.previous()
        return first.start, last.end
    return last.start, last.end


def timeframe_label(schedule: ExportSchedule, first_day: date, last_day: date) -> str:
    """Der Zeitraum, wie er in Betreff und Dateiname steht."""
    period = period_for(first_day, _start_day(schedule))
    if period.start == first_day and period.end == last_day:
        return period.label
    return f"{first_day:%d.%m.%Y} bis {last_day:%d.%m.%Y}"


# --- Lauf sichern -----------------------------------------------------------


def _claim(schedule: ExportSchedule, due_on: date) -> ExportRun | None:
    """Sichert sich den Lauf zu dieser Fälligkeit, oder gibt None zurück.

    Siehe Modulkopf: erst anlegen, sonst bedingt übernehmen.
    """
    now = timezone.now()
    try:
        with transaction.atomic():
            return ExportRun.objects.create(
                schedule=schedule,
                due_on=due_on,
                status=ExportRun.Status.RUNNING,
                started_at=now,
            )
    except IntegrityError:
        pass

    run = ExportRun.objects.filter(schedule=schedule, due_on=due_on).first()
    if run is None or run.status in (ExportRun.Status.SENT, ExportRun.Status.BLOCKED):
        return None
    if run.status == ExportRun.Status.RUNNING and run.started_at > now - STALE_AFTER:
        # Ein anderer Prozess ist gerade dran.
        return None

    taken = ExportRun.objects.filter(
        pk=run.pk, status=run.status, started_at=run.started_at
    ).update(
        status=ExportRun.Status.RUNNING,
        started_at=now,
        attempts=F("attempts") + 1,
        message="",
    )
    if not taken:
        return None
    run.refresh_from_db()
    return run


def _finish(run: ExportRun, status: str, message: str = "", **fields) -> None:
    """Schreibt das Ergebnis eines Laufs fest."""
    run.status = status
    run.message = message
    run.finished_at = timezone.now()
    for name, value in fields.items():
        setattr(run, name, value)
    run.save(update_fields=["status", "message", "finished_at", *fields.keys()])


# --- Export bauen -----------------------------------------------------------


def build_attachment(
    schedule: ExportSchedule, first_day: date, last_day: date
) -> tuple[str, bytes, str, int]:
    """Baut die Datei zum Plan: Dateiname, Inhalt, Typ und Zeilenzahl.

    Ausgewertet wird mit den Rechten von `created_by`: `query_entries`
    begrenzt die Einträge auf dessen lesbare Gruppen.
    """
    user = schedule.created_by
    profile = schedule.profile
    filters = profile.filters or {}
    columns = resolve(list(profile.columns or []))

    entries = services.query_entries(
        user,
        start=first_day,
        end=last_day,
        group_ids=filters.get("groups") or None,
        user_ids=filters.get("users") or None,
        activity_ids=filters.get("activities") or None,
    )
    closed = ClosedPeriods(readable_groups(user).values_list("pk", flat=True))
    rows = services.build_rows(
        entries, profile.grouping, closed, first_day=first_day, last_day=last_day
    )
    if len(rows) > max_rows():
        raise ExportTooLarge(
            f"Der Export hat {len(rows)} Zeilen, erlaubt sind {max_rows()}. "
            "Bitte den Zeitraum oder die Filter der Vorlage enger fassen."
        )

    stem = slugify(f"{profile.name}-{timeframe_label(schedule, first_day, last_day)}") or "zeiten"
    if schedule.export_format == ExportSchedule.Format.CSV:
        dialect = CSV_DIALECTS.get(filters.get("csv_dialect") or "de", CSV_DIALECTS["de"])
        content = b"".join(
            services.csv_bytes(
                rows,
                columns,
                delimiter=dialect["delimiter"],
                decimal_separator=dialect["decimal"],
                with_bom=dialect["with_bom"],
            )
        )
        return f"zeiten_{stem}.csv", content, CSV_MIMETYPE, len(rows)

    return f"zeiten_{stem}.xlsx", services.to_xlsx(rows, columns), XLSX_MIMETYPE, len(rows)


# --- Versand ----------------------------------------------------------------


def _log_failure(schedule: ExportSchedule, note: str) -> None:
    """Ein misslungener Versand steht im Protokoll, nicht nur am Lauf."""
    log(
        AuditLog.Action.NOTIFICATION_FAILED,
        actor=schedule.created_by,
        target=schedule,
        note=f"Geplanter Export „{schedule.profile.name}“: {note}",
    )


def _log_success(
    schedule: ExportSchedule, first_day: date, last_day: date, row_count: int, size: int
) -> None:
    """Ein geplanter Versand steht im Protokoll wie ein Export von Hand."""
    log(
        AuditLog.Action.EXPORT,
        actor=schedule.created_by,
        target=schedule,
        note=(
            f"{schedule.get_export_format_display()}-Export nach Zeitplan "
            f"„{schedule.profile.name}“, {row_count} Zeilen, "
            f"{first_day} bis {last_day}, {size} Bytes, "
            f"an {schedule.recipients_label}"
        ),
    )


def run_schedule(schedule: ExportSchedule, run: ExportRun) -> bool:
    """Baut den Export eines Plans und verschickt ihn. Gibt zurück, ob es geklappt hat."""
    first_day, last_day = timeframe_for(schedule, run.due_on)
    period = {"period_start": first_day, "period_end": last_day}

    # Rechte werden beim Ausführen erneut geprüft, nicht nur beim Anlegen:
    # aus einem Gruppen-Admin kann inzwischen ein einfaches Mitglied
    # geworden sein, und dann darf über den Plan nichts mehr herausgehen.
    if not may_schedule(schedule.created_by, schedule.profile):
        ExportSchedule.objects.filter(pk=schedule.pk).update(is_active=False)
        note = (
            f"{schedule.created_by.full_name} darf die Vorlage nicht mehr benutzen. "
            "Der Zeitplan wurde deaktiviert."
        )
        _finish(run, ExportRun.Status.BLOCKED, note, **period)
        _log_failure(schedule, note)
        return False

    if not schedule.recipient_list:
        note = "Es ist kein Empfänger hinterlegt."
        _finish(run, ExportRun.Status.BLOCKED, note, **period)
        _log_failure(schedule, note)
        return False

    if not emails_enabled():
        # Ohne Mailserver wird nichts still verworfen: der Lauf bleibt
        # fehlgeschlagen und wird beim nächsten Mal erneut versucht.
        note = (
            "Der Mailversand für geplante Exporte ist nicht eingeschaltet "
            "(EXPORT_EMAILS_ENABLED). Der Export wurde nicht verschickt."
        )
        _finish(run, ExportRun.Status.FAILED, note, **period)
        _log_failure(schedule, note)
        return False

    try:
        filename, content, mimetype, row_count = build_attachment(schedule, first_day, last_day)
    except ExportTooLarge as exc:
        _finish(run, ExportRun.Status.BLOCKED, str(exc), **period)
        _log_failure(schedule, str(exc))
        return False

    size = len(content)
    if size > max_attachment_bytes():
        note = (
            f"Der Anhang ist mit {size // 1024} KB zu groß für eine Mail "
            f"(erlaubt sind {max_attachment_bytes() // (1024 * 1024)} MB). "
            "Bitte den Zeitraum, die Spalten oder die Filter der Vorlage enger fassen."
        )
        _finish(run, ExportRun.Status.BLOCKED, note, row_count=row_count, size_bytes=size, **period)
        _log_failure(schedule, note)
        return False

    label = timeframe_label(schedule, first_day, last_day)
    sent, reason = send_mail_with_attachment(
        f"Zeiterfassung: Export „{schedule.profile.name}“ für {label}",
        "reporting/mail/geplanter_export.txt",
        {
            "schedule": schedule,
            "profile": schedule.profile,
            "label": label,
            "first_day": first_day,
            "last_day": last_day,
            "row_count": row_count,
            "filename": filename,
        },
        schedule.recipient_list,
        filename=filename,
        content=content,
        mimetype=mimetype,
    )
    if not sent:
        # Den Fehler des Mailservers hat send_mail_with_attachment schon
        # protokolliert; hier bleibt der fehlgeschlagene Lauf, der beim
        # nächsten Durchgang erneut versucht wird.
        _finish(
            run, ExportRun.Status.FAILED, reason, row_count=row_count, size_bytes=size, **period
        )
        return False

    _finish(run, ExportRun.Status.SENT, "", row_count=row_count, size_bytes=size, **period)
    _log_success(schedule, first_day, last_day, row_count, size)
    return True


def send_due_exports(today: date | None = None) -> Result:
    """Arbeitet alle fälligen Zeitpläne ab.

    Ein Fehler in einem Plan hält die übrigen nicht auf: er landet am Lauf
    und im Protokoll, danach geht es mit dem nächsten Plan weiter.
    """
    today = today or timezone.localdate()
    sent = failed = skipped = 0

    schedules = (
        ExportSchedule.objects.active()
        .select_related("profile", "profile__owner", "created_by", "period_group")
        .order_by("pk")
    )
    for schedule in schedules:
        due_on = due_date(schedule, today)
        if due_on < timezone.localtime(schedule.created_at).date():
            # Vor dem Anlegen des Plans wird nichts nachgeholt.
            skipped += 1
            continue

        run = _claim(schedule, due_on)
        if run is None:
            skipped += 1
            continue

        try:
            ok = run_schedule(schedule, run)
        except Exception as exc:  # noqa: BLE001
            # Nichts darf den Lauf der übrigen Pläne abbrechen.
            _finish(run, ExportRun.Status.FAILED, f"Unerwarteter Fehler: {exc}")
            _log_failure(schedule, f"Unerwarteter Fehler: {exc}")
            failed += 1
            continue

        if ok:
            sent += 1
        else:
            failed += 1

    return Result(sent=sent, failed=failed, skipped=skipped)
