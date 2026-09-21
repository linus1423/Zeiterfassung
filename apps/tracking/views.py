from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.groups.periods import member_start_day, period_for

from . import services
from .daysplit import entries_in_range, parts_in_range
from .forms import ClockInForm, MyEntriesFilterForm, SwitchActivityForm
from .models import BreakEntry, TimeEntry

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
    today = timezone.localdate()
    # Auch der Eintrag von gestern Abend, der über Mitternacht läuft (Issue 32).
    today_entries = list(
        _entries_with_breaks(
            entries_in_range(TimeEntry.objects.filter(user=request.user), today, today)
        ).order_by("start")
    )

    today_parts = parts_in_range(today_entries, today, today)
    worked_today = sum((part.work for part in today_parts), timedelta())
    paused_today = sum((part.breaks for part in today_parts), timedelta())

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
        # Geprüft wird der ganze Tag, nicht der einzelne Eintrag (Issue 49).
        "statutory_warnings": services.statutory_warnings(request.user, today, today),
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
        entry = services.clock_out(request.user)
    except services.ClockError as exc:
        messages.error(request, str(exc))
        return redirect("tracking:clock")

    messages.success(request, "Ausgestempelt.")
    # Hinweis auf verletzte Arbeitszeitregeln, sobald der Tag feststeht
    # (Issue 49). Abgezogen wird nie etwas, nur gewarnt.
    for text in services.warnings_for_entry(entry):
        messages.warning(request, text)
    return redirect("tracking:clock")


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


def _week_totals(parts) -> list[dict]:
    """Summen je Kalenderwoche, neueste zuerst."""
    weeks: dict[tuple[int, int], dict] = {}
    for part in parts:
        day = part.day
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
        week["total"] += part.work
    return [weeks[key] for key in sorted(weeks, reverse=True)]


def _my_export_response(entries, start_day: date, end_day: date, export: str):
    """Die eigenen Zeiten als Excel oder CSV, mit fester Spaltenliste."""
    from apps.reporting import services as reporting
    from apps.reporting.columns import resolve

    columns = resolve(list(MY_EXPORT_COLUMNS))
    rows = reporting.build_rows(
        [entry for entry in entries if entry.end is not None],
        "entry",
        first_day=start_day,
        last_day=end_day,
    )
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
    start_day_of_cycle = member_start_day(request.user)
    default_start = period_for(today, start_day_of_cycle).start

    quick = request.GET.get("bereich", "")
    quick_range = _quick_range(quick, start_day_of_cycle, today)
    data = {"start": default_start, "end": today}
    data.update(request.GET.dict())
    if quick_range is not None:
        data["start"], data["end"] = quick_range

    form = MyEntriesFilterForm(request.user, data)
    is_valid = form.is_valid()
    start_day, end_day = default_start, today
    if is_valid:
        start_day = form.cleaned_data["start"]
        end_day = form.cleaned_data["end"]

    # Einträge, die vor dem Zeitraum beginnen und hineinlaufen, gehören dazu;
    # gezählt wird davon nur der Anteil im Zeitraum (Issue 32).
    queryset = entries_in_range(TimeEntry.objects.filter(user=request.user), start_day, end_day)
    activity = form.cleaned_data.get("activity") if is_valid else None
    if activity is not None:
        queryset = queryset.filter(activity=activity)

    entries = list(_entries_with_breaks(queryset).order_by("-start"))

    export = request.GET.get("export", "")
    if export in ("csv", "xlsx"):
        # Bei fehlerhaften Eingaben gäbe es sonst eine Datei mit dem
        # Vorgabezeitraum statt der Fehlermeldung.
        if is_valid:
            return _my_export_response(entries, start_day, end_day, export)
        messages.error(request, "Bitte zuerst die Eingaben im Filter berichtigen.")

    parts = parts_in_range(entries, start_day, end_day)
    by_day: dict[date, timedelta] = {}
    for part in parts:
        by_day[part.day] = by_day.get(part.day, timedelta()) + part.work

    context = {
        "form": form,
        "entries": entries,
        "total": sum((part.work for part in parts), timedelta()),
        "by_day": sorted(by_day.items(), reverse=True),
        "by_week": _week_totals(parts),
        "start_day": start_day,
        "end_day": end_day,
        "quick": quick,
        "activity": activity,
    }
    return render(request, "tracking/my_entries.html", context)
