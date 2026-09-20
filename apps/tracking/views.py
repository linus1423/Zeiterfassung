from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.groups.models import Group
from apps.groups.periods import period_for

from . import services
from .forms import ClockInForm, MyEntriesFilterForm, SwitchActivityForm
from .models import BreakEntry, TimeEntry
from .utils import day_bounds

# Spalten des Downloads der eigenen Zeiten. Bewusst fest: die frei wählbare
# Zusammenstellung bleibt der Auswertung vorbehalten.
MY_EXPORT_COLUMNS = (
    "date",
    "weekday",
    "group",
    "activity",
    "start",
    "end",
    "break_hhmm",
    "hours",
    "hhmm",
    "note",
)


def _entries_with_breaks(queryset):
    # "user" gehört dazu, weil der Download je Zeile auf die Person zugreift.
    return queryset.select_related("user", "group", "activity").prefetch_related(
        Prefetch("breaks", queryset=BreakEntry.objects.order_by("start"))
    )


@login_required
def clock(request):
    """Stempeluhr: Zustand, Knöpfe und die Einträge von heute."""
    state = services.get_state(request.user)
    today_start, today_end = day_bounds(timezone.localdate())
    today_entries = list(
        _entries_with_breaks(
            TimeEntry.objects.filter(user=request.user, start__gte=today_start, start__lt=today_end)
        ).order_by("start")
    )

    worked_today = sum((entry.duration for entry in today_entries), timedelta())
    paused_today = sum((entry.break_duration for entry in today_entries), timedelta())

    incomplete = TimeEntry.objects.filter(
        user=request.user, is_incomplete=True, start__gte=timezone.now() - timedelta(days=30)
    ).order_by("-start")[:5]

    context = {
        "state": state,
        "form": ClockInForm(request.user),
        "switch_form": SwitchActivityForm(state.entry) if state.entry is not None else None,
        "today_entries": today_entries,
        "worked_today": worked_today,
        "paused_today": paused_today,
        "break_warning": services.statutory_break_warning(worked_today, paused_today),
        "incomplete_entries": incomplete,
        "has_groups": bool(request.user.member_group_ids()),
    }
    return render(request, "tracking/clock.html", context)


@require_POST
@login_required
def clock_in_view(request):
    form = ClockInForm(request.user, request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte Gruppe und Tätigkeit auswählen.")
        return redirect("tracking:clock")

    try:
        services.clock_in(
            request.user,
            form.cleaned_data["group"],
            form.cleaned_data["activity"],
            note=form.cleaned_data.get("note", ""),
        )
        messages.success(request, "Eingestempelt.")
    except services.ClockError as exc:
        messages.error(request, str(exc))
    return redirect("tracking:clock")


@require_POST
@login_required
def switch_activity_view(request):
    """Tätigkeit wechseln, ohne aus- und wieder einzustempeln."""
    state = services.get_state(request.user)
    if state.entry is None:
        messages.error(request, "Du bist gerade nicht eingestempelt.")
        return redirect("tracking:clock")

    form = SwitchActivityForm(state.entry, request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte eine Tätigkeit auswählen.")
        return redirect("tracking:clock")

    try:
        entry = services.switch_activity(request.user, form.cleaned_data["activity"])
        messages.success(request, f"Weiter auf {entry.activity.name}.")
    except services.ClockError as exc:
        messages.error(request, str(exc))
    return redirect("tracking:clock")


@require_POST
@login_required
def break_start_view(request):
    try:
        services.start_break(request.user)
        messages.success(request, "Pause begonnen.")
    except services.ClockError as exc:
        messages.error(request, str(exc))
    return redirect("tracking:clock")


@require_POST
@login_required
def break_end_view(request):
    try:
        services.end_break(request.user)
        messages.success(request, "Pause beendet.")
    except services.ClockError as exc:
        messages.error(request, str(exc))
    return redirect("tracking:clock")


@require_POST
@login_required
def clock_out_view(request):
    try:
        services.clock_out(request.user)
        messages.success(request, "Ausgestempelt.")
    except services.ClockError as exc:
        messages.error(request, str(exc))
    return redirect("tracking:clock")


def _billing_start_day(user) -> int:
    """Der Zyklusbeginn der Gruppen des Nutzers, sonst der Kalendermonat.

    Gehört jemand zu Gruppen mit unterschiedlichen Zyklen, gibt es keinen
    richtigen Vorgabewert; dann bleibt es beim Kalendermonat.
    """
    start_days = set(
        Group.objects.filter(pk__in=user.member_group_ids()).values_list(
            "month_start_day", flat=True
        )
    )
    return start_days.pop() if len(start_days) == 1 else 1


def _quick_range(name: str, start_day: int, today: date) -> tuple[date, date] | None:
    """Die Zeiträume hinter den Schnellschaltern über der Liste."""
    if name == "woche":
        monday = today - timedelta(days=today.weekday())
        return monday, today
    if name == "vorwoche":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if name == "monat":
        return period_for(today, start_day).start, today
    if name == "vormonat":
        previous = period_for(today, start_day).previous()
        return previous.start, previous.end
    return None


def _week_totals(entries) -> list[dict]:
    """Summen je Kalenderwoche, neueste zuerst."""
    weeks: dict[tuple[int, int], dict] = {}
    for entry in entries:
        day = timezone.localtime(entry.start).date()
        iso_year, iso_week, _ = day.isocalendar()
        week = weeks.get((iso_year, iso_week))
        if week is None:
            monday = day - timedelta(days=day.weekday())
            week = {
                "year": iso_year,
                "week": iso_week,
                "start": monday,
                "end": monday + timedelta(days=6),
                "total": timedelta(),
            }
            weeks[(iso_year, iso_week)] = week
        week["total"] += entry.duration
    return [weeks[key] for key in sorted(weeks, reverse=True)]


def _my_export_response(entries, start_day: date, end_day: date, export: str):
    """Die eigenen Zeiten als Excel oder CSV, mit fester Spaltenliste."""
    from apps.reporting import services as reporting
    from apps.reporting.columns import resolve

    columns = resolve(list(MY_EXPORT_COLUMNS))
    rows = reporting.build_rows([entry for entry in entries if entry.end is not None], "entry")
    filename = f"meine-zeiten_{slugify(f'{start_day:%Y-%m-%d}_bis_{end_day:%Y-%m-%d}')}"

    if export == "xlsx":
        response = HttpResponse(
            reporting.to_xlsx(rows, columns, title="Meine Zeiten"),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return response

    response = StreamingHttpResponse(
        reporting.csv_bytes(rows, columns), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    return response


@login_required
def my_entries(request):
    """Eigene Zeiten in einem wählbaren Zeitraum, mit Tages- und Wochensummen."""
    today = timezone.localdate()
    start_day_of_cycle = _billing_start_day(request.user)
    default_start = period_for(today, start_day_of_cycle).start

    quick = request.GET.get("bereich", "")
    quick_range = _quick_range(quick, start_day_of_cycle, today)
    data = {"start": default_start, "end": today}
    data.update(request.GET.dict())
    if quick_range is not None:
        data["start"], data["end"] = quick_range

    form = MyEntriesFilterForm(request.user, data)
    start_day, end_day = default_start, today
    if form.is_valid():
        start_day = form.cleaned_data["start"]
        end_day = form.cleaned_data["end"]

    period_start, _ = day_bounds(start_day)
    _, period_end = day_bounds(end_day)

    queryset = TimeEntry.objects.filter(
        user=request.user, start__gte=period_start, start__lt=period_end
    )
    activity = form.cleaned_data.get("activity") if form.is_valid() else None
    if activity is not None:
        queryset = queryset.filter(activity=activity)

    entries = list(_entries_with_breaks(queryset).order_by("-start"))

    export = request.GET.get("export", "")
    if export in ("csv", "xlsx"):
        return _my_export_response(entries, start_day, end_day, export)

    by_day: dict[date, timedelta] = {}
    for entry in entries:
        day = timezone.localtime(entry.start).date()
        by_day[day] = by_day.get(day, timedelta()) + entry.duration

    context = {
        "form": form,
        "entries": entries,
        "total": sum((entry.duration for entry in entries), timedelta()),
        "by_day": sorted(by_day.items(), reverse=True),
        "by_week": _week_totals(entries),
        "start_day": start_day,
        "end_day": end_day,
        "quick": quick,
        "activity": activity,
    }
    return render(request, "tracking/my_entries.html", context)
