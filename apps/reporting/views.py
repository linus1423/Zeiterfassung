from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit.models import AuditLog, log
from apps.groups.closing import ClosedPeriods
from apps.groups.periods import member_start_day, period_for
from apps.groups.permissions import readable_groups, require_group_read
from apps.tracking.forms import PeriodForm

from . import schedules as schedule_jobs
from . import services, timesheet
from .access import (
    may_change_schedule,
    require_reporting_access,
    visible_profiles,
    visible_schedules,
)
from .columns import resolve
from .forms import CSV_DIALECTS, ExportForm, ExportScheduleForm, ProfileSaveForm
from .models import ExportProfile, ExportSchedule

PREVIEW_ROWS = 25


@login_required
def export_view(request, profile_id=None):
    """Auswertung mit Vorschau und Download als Excel oder CSV."""
    require_reporting_access(request.user)

    profile = None
    initial = None
    if profile_id is not None:
        profile = get_object_or_404(visible_profiles(request.user), pk=profile_id)
        initial = dict(profile.filters or {})
        initial.update({"columns": profile.columns, "grouping": profile.grouping})

    if request.method == "POST":
        form = ExportForm(request.user, request.POST)
    elif initial:
        form = ExportForm(request.user, initial=initial)
    else:
        today = timezone.localdate()
        form = ExportForm(request.user, initial={"start": today.replace(day=1), "end": today})

    rows = []
    columns = []
    if request.method == "POST" and form.is_valid():
        columns = resolve(form.ordered_columns())
        entries = services.query_entries(
            request.user,
            start=form.cleaned_data["start"],
            end=form.cleaned_data["end"],
            group_ids=[group.pk for group in form.cleaned_data["groups"]],
            user_ids=[user.pk for user in form.cleaned_data["users"]],
            activity_ids=[activity.pk for activity in form.cleaned_data["activities"]],
        )
        closed = ClosedPeriods(readable_groups(request.user).values_list("pk", flat=True))
        rows = services.build_rows(
            entries,
            form.cleaned_data["grouping"],
            closed,
            first_day=form.cleaned_data["start"],
            last_day=form.cleaned_data["end"],
        )

        action = request.POST.get("action", "preview")
        if action == "xlsx":
            return _xlsx_response(request, rows, columns, form)
        if action == "csv":
            return _csv_response(request, rows, columns, form)
        if action == "save_profile":
            return _save_profile(request, form)

    context = {
        "form": form,
        "profile": profile,
        "profiles": visible_profiles(request.user),
        "profile_form": ProfileSaveForm(),
        "columns": columns,
        "preview_rows": [
            [services.format_value(row, column) for column in columns]
            for row in rows[:PREVIEW_ROWS]
        ],
        "row_count": len(rows),
        "preview_limit": PREVIEW_ROWS,
    }
    return render(request, "reporting/export.html", context)


def _period_label(form) -> str:
    return f"{form.cleaned_data['start']:%Y-%m-%d}_bis_{form.cleaned_data['end']:%Y-%m-%d}"


def _log_export(request, form, row_count: int, export_format: str) -> None:
    log(
        AuditLog.Action.EXPORT,
        actor=request.user,
        note=(
            f"{export_format.upper()}-Export, {row_count} Zeilen, "
            f"{form.cleaned_data['start']} bis {form.cleaned_data['end']}"
        ),
    )


def _xlsx_response(request, rows, columns, form):
    content = services.to_xlsx(rows, columns, title="Zeiten")
    _log_export(request, form, len(rows), "xlsx")
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    filename = f"zeiten_{slugify(_period_label(form))}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _csv_response(request, rows, columns, form):
    dialect = CSV_DIALECTS.get(form.cleaned_data.get("csv_dialect") or "de", CSV_DIALECTS["de"])
    _log_export(request, form, len(rows), "csv")

    # Das BOM gehört genau einmal an den Anfang, nicht an jeden Block, sonst
    # zeigt Excel Steuerzeichen mitten in der Datei.
    stream = services.csv_bytes(
        rows,
        columns,
        delimiter=dialect["delimiter"],
        decimal_separator=dialect["decimal"],
        with_bom=dialect["with_bom"],
    )
    response = StreamingHttpResponse(stream, content_type="text/csv; charset=utf-8")
    filename = f"zeiten_{slugify(_period_label(form))}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _save_profile(request, form):
    profile_form = ProfileSaveForm(request.POST)
    if not profile_form.is_valid():
        messages.error(request, "Bitte einen Namen für die Vorlage angeben.")
        return redirect("reporting:export")

    filters = {
        "start": form.cleaned_data["start"].isoformat(),
        "end": form.cleaned_data["end"].isoformat(),
        "groups": [group.pk for group in form.cleaned_data["groups"]],
        "users": [user.pk for user in form.cleaned_data["users"]],
        "activities": [activity.pk for activity in form.cleaned_data["activities"]],
        "csv_dialect": form.cleaned_data.get("csv_dialect") or "de",
    }
    ExportProfile.objects.update_or_create(
        owner=request.user,
        name=profile_form.cleaned_data["name"],
        defaults={
            "is_shared": profile_form.cleaned_data["is_shared"],
            "columns": form.ordered_columns(),
            "grouping": form.cleaned_data["grouping"],
            "filters": filters,
        },
    )
    messages.success(request, "Vorlage gespeichert.")
    return redirect("reporting:export")


@require_POST
@login_required
def profile_delete(request, profile_id):
    profile = get_object_or_404(ExportProfile, pk=profile_id, owner=request.user)
    profile.delete()
    messages.success(request, "Vorlage gelöscht.")
    return redirect("reporting:export")


@login_required
def schedule_list(request):
    """Geplante Exporte anlegen und ansehen (Issue 55).

    Einrichten darf nur, wer die Auswertung benutzen darf und die Vorlage
    sehen kann; der Export läuft später mit genau diesen Rechten.
    """
    require_reporting_access(request.user)

    if request.method == "POST":
        form = ExportScheduleForm(request.user, request.POST)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.created_by = request.user
            schedule.save()
            messages.success(
                request,
                f"Zeitplan gespeichert: am {schedule.day_of_month}. jedes Monats "
                f"an {schedule.recipients_label}.",
            )
            return redirect("reporting:schedules")
    else:
        form = ExportScheduleForm(request.user)

    return render(
        request,
        "reporting/schedules.html",
        {
            "form": form,
            "schedules": visible_schedules(request.user),
            "emails_enabled": schedule_jobs.emails_enabled(),
            "max_attachment_mb": schedule_jobs.max_attachment_bytes() // (1024 * 1024),
        },
    )


@require_POST
@login_required
def schedule_delete(request, schedule_id):
    require_reporting_access(request.user)
    schedule = get_object_or_404(ExportSchedule.objects.select_related("profile"), pk=schedule_id)
    if not may_change_schedule(request.user, schedule):
        raise PermissionDenied("Diesen Zeitplan darfst du nicht löschen.")
    schedule.delete()
    messages.success(request, "Zeitplan gelöscht.")
    return redirect("reporting:schedules")


def _sheet_range(request, start_day_of_cycle: int):
    """Zeitraum des Nachweises: aus dem Formular, sonst der laufende Zyklus."""
    today = timezone.localdate()
    period = period_for(today, start_day_of_cycle)
    form = PeriodForm(request.GET or {"start": period.start, "end": period.end})
    if form.is_valid():
        first, last = form.cleaned_data["start"], form.cleaned_data["end"]
        if (last - first).days >= timesheet.MAX_DAYS:
            form.add_error("end", "Ein Nachweis umfasst höchstens ein Jahr.")
        else:
            return form, first, last
    return form, period.start, period.end


@login_required
def timesheet_view(request, user_id):
    """Arbeitszeitnachweis einer Person zum Ausdrucken (Issue 35)."""
    person = get_object_or_404(get_user_model(), pk=user_id)
    if not timesheet.may_see(request.user, person):
        raise PermissionDenied("Kein Zugriff auf diesen Nachweis.")

    form, first_day, last_day = _sheet_range(request, member_start_day(person))
    return render(
        request,
        "reporting/timesheet.html",
        {
            "form": form,
            "sheets": [
                timesheet.build(
                    person, first_day, last_day, timesheet.visible_groups(request.user, person)
                )
            ],
            "first_day": first_day,
            "last_day": last_day,
            "title": f"Arbeitszeitnachweis {person.full_name}",
        },
    )


@login_required
def group_timesheets(request, group_id):
    """Ein Nachweis je Mitglied einer Gruppe, alle auf einmal zum Drucken."""
    group = require_group_read(request.user, group_id)

    form, first_day, last_day = _sheet_range(request, group.month_start_day)
    return render(
        request,
        "reporting/timesheet.html",
        {
            "form": form,
            "sheets": timesheet.build_for_group(group, first_day, last_day),
            "first_day": first_day,
            "last_day": last_day,
            "group": group,
            "title": f"Arbeitszeitnachweise {group.name}",
        },
    )
