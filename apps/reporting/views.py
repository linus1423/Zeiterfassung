from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit.models import AuditLog, log
from apps.groups.closing import ClosedPeriods
from apps.groups.models import PeriodLock
from apps.groups.permissions import readable_groups

from . import services
from .columns import resolve
from .forms import CSV_DIALECTS, ExportForm, ProfileSaveForm
from .models import ExportProfile

PREVIEW_ROWS = 25


def _require_reporting_access(user):
    """Auswertung sehen: Buchhaltung, System-Admin oder Admin mindestens einer Gruppe."""
    if not (user.sees_all_groups or user.is_any_group_admin):
        raise PermissionDenied("Kein Zugriff auf die Auswertung.")


def _visible_profiles(user):
    return ExportProfile.objects.filter(
        Q(owner=user) | Q(is_shared=True, owner__is_accounting=True)
    ).select_related("owner")


@login_required
def export_view(request, profile_id=None):
    """Auswertung mit Vorschau und Download als Excel oder CSV."""
    _require_reporting_access(request.user)

    profile = None
    initial = None
    if profile_id is not None:
        profile = get_object_or_404(_visible_profiles(request.user), pk=profile_id)
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
        rows = services.build_rows(entries, form.cleaned_data["grouping"], closed)

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
        "profiles": _visible_profiles(request.user),
        "profile_form": ProfileSaveForm(),
        "columns": columns,
        "preview_rows": [
            [services.format_value(row, column) for column in columns]
            for row in rows[:PREVIEW_ROWS]
        ],
        "row_count": len(rows),
        "preview_limit": PREVIEW_ROWS,
        "closed_periods": _latest_closed_periods(request.user),
    }
    return render(request, "reporting/export.html", context)


def _latest_closed_periods(user):
    """Der jeweils letzte Abschluss je Gruppe, damit die Buchhaltung ihn sieht."""
    locks = (
        PeriodLock.objects.filter(group__in=readable_groups(user))
        .select_related("group")
        .order_by("group__name", "-period_start")
    )
    latest = {}
    for lock in locks:
        latest.setdefault(lock.group_id, lock)
    return sorted(latest.values(), key=lambda lock: lock.group.name)


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
