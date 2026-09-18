from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.tracking.forms import PeriodForm
from apps.tracking.models import BreakEntry, TimeEntry
from apps.tracking.utils import day_bounds

from .forms import ActivityForm, MembershipForm
from .models import Activity, GroupMembership
from .permissions import readable_groups, require_group_admin, require_group_read


@login_required
def group_list(request):
    """Gruppen, die der Nutzer auswerten darf."""
    groups = readable_groups(request.user).annotate(member_count=Count("memberships"))
    return render(request, "groups/group_list.html", {"groups": groups})


@login_required
def group_detail(request, group_id):
    """Wer arbeitet gerade, und wie verteilen sich die Zeiten im Zeitraum."""
    group = require_group_read(request.user, group_id)

    today = timezone.localdate()
    form = PeriodForm(request.GET or {"start": today.replace(day=1), "end": today})
    start_day, end_day = today.replace(day=1), today
    if form.is_valid():
        start_day, end_day = form.cleaned_data["start"], form.cleaned_data["end"]

    period_start, _ = day_bounds(start_day)
    _, period_end = day_bounds(end_day)

    entries = list(
        TimeEntry.objects.filter(group=group, start__gte=period_start, start__lt=period_end)
        .select_related("user", "activity")
        .prefetch_related(Prefetch("breaks", queryset=BreakEntry.objects.order_by("start")))
        .order_by("-start")
    )

    per_user: dict[str, timedelta] = {}
    per_activity: dict[str, timedelta] = {}
    for entry in entries:
        per_user[entry.user.full_name] = (
            per_user.get(entry.user.full_name, timedelta()) + entry.duration
        )
        label = entry.activity.name if entry.activity else "ohne Taetigkeit"
        per_activity[label] = per_activity.get(label, timedelta()) + entry.duration

    context = {
        "group": group,
        "form": form,
        "entries": entries[:200],
        "entry_count": len(entries),
        "currently_working": TimeEntry.objects.open()
        .filter(group=group)
        .select_related("user", "activity")
        .prefetch_related("breaks"),
        "per_user": sorted(per_user.items()),
        "per_activity": sorted(per_activity.items()),
        "total": sum((entry.duration for entry in entries), timedelta()),
        "is_admin": request.user.is_group_admin(group),
    }
    return render(request, "groups/group_detail.html", context)


@login_required
def activity_list(request, group_id):
    group = require_group_admin(request.user, group_id)
    form = ActivityForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            activity = form.save(commit=False)
            activity.group = group
            if Activity.objects.filter(group=group, name__iexact=activity.name).exists():
                form.add_error("name", "Diese Taetigkeit gibt es in der Gruppe schon.")
            else:
                activity.save()
                messages.success(request, "Taetigkeit angelegt.")
                return redirect("groups:activities", group_id=group.pk)

    return render(
        request,
        "groups/activity_list.html",
        {"group": group, "form": form, "activities": group.activities.all()},
    )


@login_required
def activity_edit(request, group_id, activity_id):
    group = require_group_admin(request.user, group_id)
    activity = get_object_or_404(Activity, pk=activity_id, group=group)
    form = ActivityForm(request.POST or None, instance=activity)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Taetigkeit gespeichert.")
        return redirect("groups:activities", group_id=group.pk)

    return render(
        request, "groups/activity_form.html", {"group": group, "form": form, "activity": activity}
    )


@login_required
def member_list(request, group_id):
    group = require_group_admin(request.user, group_id)
    form = MembershipForm(group, request.POST or None)

    if request.method == "POST" and form.is_valid():
        GroupMembership.objects.create(
            group=group, user=form.cleaned_user, role=form.cleaned_data["role"]
        )
        messages.success(request, f"{form.cleaned_user} wurde der Gruppe hinzugefuegt.")
        return redirect("groups:members", group_id=group.pk)

    memberships = group.memberships.select_related("user").order_by(
        "user__last_name", "user__first_name"
    )
    return render(
        request,
        "groups/member_list.html",
        {"group": group, "form": form, "memberships": memberships},
    )


@require_POST
@login_required
def member_role(request, group_id, membership_id):
    """Admin-Rolle vergeben oder entziehen."""
    group = require_group_admin(request.user, group_id)
    membership = get_object_or_404(GroupMembership, pk=membership_id, group=group)

    if (
        membership.is_admin
        and group.memberships.filter(role=GroupMembership.Role.ADMIN).count() == 1
    ):
        messages.error(request, "Die Gruppe braucht mindestens einen Admin.")
        return redirect("groups:members", group_id=group.pk)

    membership.role = (
        GroupMembership.Role.MEMBER if membership.is_admin else GroupMembership.Role.ADMIN
    )
    membership.save(update_fields=["role"])
    messages.success(request, f"Rolle von {membership.user} geaendert.")
    return redirect("groups:members", group_id=group.pk)


@require_POST
@login_required
def member_remove(request, group_id, membership_id):
    group = require_group_admin(request.user, group_id)
    membership = get_object_or_404(GroupMembership, pk=membership_id, group=group)

    if TimeEntry.objects.open().filter(user=membership.user, group=group).exists():
        messages.error(
            request, "Der Nutzer ist gerade eingestempelt und kann nicht entfernt werden."
        )
        return redirect("groups:members", group_id=group.pk)

    if (
        membership.is_admin
        and group.memberships.filter(role=GroupMembership.Role.ADMIN).count() == 1
    ):
        messages.error(request, "Die Gruppe braucht mindestens einen Admin.")
        return redirect("groups:members", group_id=group.pk)

    user = membership.user
    membership.delete()
    messages.success(request, f"{user} wurde aus der Gruppe entfernt.")
    return redirect("groups:members", group_id=group.pk)
