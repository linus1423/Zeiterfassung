from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import ClockInForm, PeriodForm
from .models import BreakEntry, TimeEntry
from .utils import day_bounds


def _entries_with_breaks(queryset):
    return queryset.select_related("group", "activity").prefetch_related(
        Prefetch("breaks", queryset=BreakEntry.objects.order_by("start"))
    )


@login_required
def clock(request):
    """Stempeluhr: Zustand, Knoepfe und die Eintraege von heute."""
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
        messages.error(request, "Bitte Gruppe und Taetigkeit auswaehlen.")
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


@login_required
def my_entries(request):
    """Eigene Zeiten in einem waehlbaren Zeitraum, mit Tages- und Gesamtsumme."""
    today = timezone.localdate()
    default_start = today.replace(day=1)
    form = PeriodForm(request.GET or {"start": default_start, "end": today})
    start_day, end_day = default_start, today
    if form.is_valid():
        start_day = form.cleaned_data["start"]
        end_day = form.cleaned_data["end"]

    period_start, _ = day_bounds(start_day)
    _, period_end = day_bounds(end_day)

    entries = list(
        _entries_with_breaks(
            TimeEntry.objects.filter(
                user=request.user, start__gte=period_start, start__lt=period_end
            )
        ).order_by("-start")
    )

    by_day: dict[date, timedelta] = {}
    for entry in entries:
        day = timezone.localtime(entry.start).date()
        by_day[day] = by_day.get(day, timedelta()) + entry.duration

    context = {
        "form": form,
        "entries": entries,
        "total": sum((entry.duration for entry in entries), timedelta()),
        "by_day": sorted(by_day.items(), reverse=True),
        "start_day": start_day,
        "end_day": end_day,
    }
    return render(request, "tracking/my_entries.html", context)
