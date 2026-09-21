from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.audit.models import AuditLog, log
from apps.groups.closing import ClosedPeriods
from apps.groups.periods import QUICK_RANGES, member_start_day, period_for, quick_range
from apps.groups.permissions import readable_groups, require_group_read
from apps.tracking.forms import PeriodForm

from . import services, summary, timesheet
from .columns import resolve
from .forms import CSV_DIALECTS, ExportForm, ProfileSaveForm, SummaryForm
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


# Felder, die die Auswertungsseite an den Export weiterreicht.
_QUERY_FIELDS = ("start", "end")
_QUERY_LISTS = ("groups", "users", "activities", "cost_centers")


def _initial_from_query(request) -> dict:
    """Zeitraum und Filter aus der Adresszeile als Vorauswahl.

    So bringt der Weg von der Auswertungsseite zum Export dieselbe Auswahl
    mit. Geprüft wird nichts: was hier steht, ist nur eine Vorbelegung des
    Formulars, und das Formular lässt ohnehin nur lesbare Gruppen zu.
    """
    initial = {}
    for name in _QUERY_FIELDS:
        value = request.GET.get(name)
        if value:
            initial[name] = value
    for name in _QUERY_LISTS:
        values = request.GET.getlist(name)
        if values:
            initial[name] = values
    return initial


@login_required
def summary_view(request):
    """Summen und Verteilung im Zeitraum, ohne Umweg über eine Datei (Issue 56).

    Die Zahlen kommen aus derselben Verdichtung wie der Export, damit Ansicht
    und Datei nie auseinanderlaufen.
    """
    _require_reporting_access(request.user)

    today = timezone.localdate()
    start_day_of_cycle = member_start_day(request.user)
    default_start = period_for(today, start_day_of_cycle).start

    quick = request.GET.get("bereich", "")
    span = quick_range(quick, start_day_of_cycle, today)
    # Eine Kopie der Adresszeile, damit die Mehrfachfelder ihre Listen behalten.
    data = request.GET.copy()
    data.setdefault("start", default_start.isoformat())
    data.setdefault("end", today.isoformat())
    if span is not None:
        data["start"], data["end"] = span[0].isoformat(), span[1].isoformat()

    form = SummaryForm(request.user, data)
    result = None
    query = {}
    first_day = last_day = None
    if form.is_valid():
        first_day, last_day = form.cleaned_data["start"], form.cleaned_data["end"]
        # Eine Abfrage, ein Durchlauf über die Tagesanteile, danach nur noch
        # Rechnen im Speicher: keine Abfrage je Zeile.
        entries = services.query_entries(request.user, **form.selection())
        result = summary.build(services.base_rows(entries, first_day=first_day, last_day=last_day))
        query = form.as_query()

    filters = {name: query.get(name, []) for name in ("groups", "activities", "cost_centers")}
    context = {
        "form": form,
        "summary": result,
        "quick": quick,
        "quick_ranges": QUICK_RANGES,
        "start_day": first_day,
        "end_day": last_day,
        # Für die Schnellschalter (ohne Zeitraum) und den Weg zum Export (mit).
        "filter_query": urlencode(filters, doseq=True),
        "export_query": urlencode(query, doseq=True),
    }
    return render(request, "reporting/summary.html", context)


@login_required
def export_view(request, profile_id=None):
    """Export mit Vorschau und Download als Excel oder CSV."""
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
        form = ExportForm(
            request.user,
            initial={
                "start": today.replace(day=1),
                "end": today,
                **_initial_from_query(request),
            },
        )

    rows = []
    columns = []
    if request.method == "POST" and form.is_valid():
        columns = resolve(form.ordered_columns())
        entries = services.query_entries(request.user, **form.selection())
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
        "profiles": _visible_profiles(request.user),
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

    selection = form.selection()
    filters = {
        "start": selection["start"].isoformat(),
        "end": selection["end"].isoformat(),
        "groups": selection["group_ids"],
        "users": selection["user_ids"],
        "activities": selection["activity_ids"],
        "cost_centers": selection["cost_centers"],
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
