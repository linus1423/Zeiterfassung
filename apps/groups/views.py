from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.models import AuditLog, log
from apps.tracking import arbzg, entry_editing
from apps.tracking.daysplit import entries_in_range, parts_in_range
from apps.tracking.forms import PeriodForm, break_formset, validate_breaks_within
from apps.tracking.models import BreakEntry, TimeEntry

from . import closing, overview
from .forms import (
    ActivityForm,
    AdminEntryForm,
    ClosePeriodForm,
    GroupForm,
    GroupSettingsForm,
    MembershipForm,
)
from .models import Activity, GroupMembership, PeriodLock
from .periods import period_for
from .permissions import readable_groups, require_group_admin, require_group_read


@login_required
def group_list(request):
    """Gruppen, die der Nutzer auswerten darf."""
    groups = readable_groups(request.user).annotate(member_count=Count("memberships"))
    admin_ids = set(request.user.admin_group_ids())
    rows = [
        {"group": group, "is_admin": request.user.is_superuser or group.pk in admin_ids}
        for group in groups
    ]
    return render(request, "groups/group_list.html", {"rows": rows})


@login_required
def closing_overview(request):
    """Wo steht der Abschluss in allen lesbaren Gruppen (Issue 36)?"""
    if not (request.user.sees_all_groups or request.user.is_any_group_admin):
        raise PermissionDenied("Kein Zugriff auf die Abschluss-Übersicht.")

    groups = readable_groups(request.user).order_by("name")
    rows = overview.closing_overview(groups)
    return render(
        request,
        "groups/closing_overview.html",
        {
            "rows": rows,
            "open_count": sum(1 for row in rows if not row.is_up_to_date),
        },
    )


@login_required
def group_create(request):
    """Eine neue Gruppe anlegen. Nur für System-Admins."""
    if not request.user.is_superuser:
        raise PermissionDenied("Nur System-Admins dürfen Gruppen anlegen.")

    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = form.save()
        log(
            AuditLog.Action.GROUP_CREATED,
            actor=request.user,
            target=group,
            group=group,
            note=f"Gruppe {group.name} angelegt.",
        )
        messages.success(
            request, f"Gruppe {group.name} angelegt. Jetzt fehlen noch die Mitglieder."
        )
        return redirect("groups:members", group_id=group.pk)

    return render(request, "groups/group_form.html", {"form": form})


@login_required
def group_detail(request, group_id):
    """Wer arbeitet gerade, und wie verteilen sich die Zeiten im Zeitraum."""
    group = require_group_read(request.user, group_id)

    today = timezone.localdate()
    period = group.current_period(today)
    form = PeriodForm(request.GET or {"start": period.start, "end": today})
    start_day, end_day = period.start, today
    if form.is_valid():
        start_day, end_day = form.cleaned_data["start"], form.cleaned_data["end"]

    # Auch Einträge, die vor dem Zeitraum beginnen und hineinlaufen; gezählt
    # wird davon nur der Anteil im Zeitraum (Issue 32).
    entries = list(
        entries_in_range(TimeEntry.objects.filter(group=group), start_day, end_day)
        .select_related("user", "activity")
        .prefetch_related(Prefetch("breaks", queryset=BreakEntry.objects.order_by("start")))
        .order_by("-start")
    )

    per_user: dict[str, timedelta] = {}
    per_activity: dict[str, timedelta] = {}
    parts = parts_in_range(entries, start_day, end_day)
    for part in parts:
        entry = part.entry
        per_user[entry.user.full_name] = per_user.get(entry.user.full_name, timedelta()) + part.work
        label = entry.activity.name if entry.activity else "ohne Tätigkeit"
        per_activity[label] = per_activity.get(label, timedelta()) + part.work

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
        "total": sum((part.work for part in parts), timedelta()),
        "is_admin": request.user.is_group_admin(group),
        "period": period,
        "period_closed": closing.is_closed(group, end_day),
    }
    return render(request, "groups/group_detail.html", context)


@login_required
def anomalies(request, group_id):
    """Auffälligkeiten nach dem Arbeitszeitgesetz je Gruppe (Issue 49).

    Lesen darf das ein Admin der Gruppe, die Buchhaltung und der System-Admin;
    geändert wird hier nichts. Gerechnet wird über den ganzen Tag, nicht über
    den einzelnen Eintrag.
    """
    group = require_group_read(request.user, group_id)

    today = timezone.localdate()
    period = group.current_period(today)
    form = PeriodForm(request.GET or {"start": period.start, "end": today})
    start_day, end_day = period.start, today
    if form.is_valid():
        start_day, end_day = form.cleaned_data["start"], form.cleaned_data["end"]

    found = arbzg.check(TimeEntry.objects.filter(group=group), start_day, end_day)
    # Neueste zuerst, innerhalb eines Tages nach Person.
    rows = sorted(found, key=lambda item: (-item.day.toordinal(), str(item.user), item.rule))

    context = {
        "group": group,
        "form": form,
        "rows": rows,
        "start_day": start_day,
        "end_day": end_day,
        "people": len({row.user.pk for row in rows}),
        "checks_enabled": bool(
            settings.STATUTORY_BREAK_WARNINGS or settings.STATUTORY_LIMIT_WARNINGS
        ),
    }
    return render(request, "groups/anomalies.html", context)


def _entry_form_page(request, group, entry):
    """Formular zum Ändern oder Nachtragen einer Zeit, für beide Fälle gleich."""
    posted = request.POST if request.method == "POST" else None
    form = AdminEntryForm(group, posted, entry=entry)
    breaks = break_formset(posted, entry=entry)

    if request.method == "POST":
        valid = form.is_valid() and breaks.is_valid()
        if valid:
            valid = validate_breaks_within(
                breaks, form.cleaned_data["start"], form.cleaned_data["end"]
            )
        if valid:
            try:
                _save_admin_entry(request, group, entry, form, breaks)
            except entry_editing.EntryEditError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Zeit geändert." if entry else "Zeit nachgetragen.")
                return redirect("groups:detail", group_id=group.pk)

    return render(
        request,
        "groups/entry_form.html",
        {"group": group, "form": form, "breaks": breaks, "entry": entry},
    )


def _save_admin_entry(request, group, entry, form, breaks):
    data = form.cleaned_data
    if entry is None:
        return entry_editing.create_entry(
            editor=request.user,
            user=data["user"],
            group=group,
            reason=data["reason"],
            start=data["start"],
            end=data["end"],
            activity=data.get("activity"),
            note=data.get("note", ""),
            breaks=breaks.breaks(),
        )
    return entry_editing.update_entry(
        entry,
        editor=request.user,
        reason=data["reason"],
        start=data["start"],
        end=data["end"],
        activity=data.get("activity"),
        note=data.get("note", ""),
        breaks=breaks.breaks(),
    )


@login_required
def entry_create(request, group_id):
    """Eine vergessene Zeit für ein Mitglied nachtragen (Issue 31)."""
    group = require_group_admin(request.user, group_id)
    return _entry_form_page(request, group, None)


@login_required
def entry_edit(request, group_id, entry_id):
    """Eine Zeit der eigenen Gruppe direkt ändern (Issue 31)."""
    group = require_group_admin(request.user, group_id)
    entry = get_object_or_404(
        TimeEntry.objects.select_related("user", "activity").prefetch_related("breaks"),
        pk=entry_id,
        group=group,
    )
    if entry.is_open:
        messages.error(request, "Ein laufender Eintrag kann nicht geändert werden.")
        return redirect("groups:detail", group_id=group.pk)
    return _entry_form_page(request, group, entry)


@login_required
def activity_list(request, group_id):
    group = require_group_admin(request.user, group_id)
    form = ActivityForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            activity = form.save(commit=False)
            activity.group = group
            if Activity.objects.filter(group=group, name__iexact=activity.name).exists():
                form.add_error("name", "Diese Tätigkeit gibt es in der Gruppe schon.")
            else:
                activity.save()
                messages.success(request, "Tätigkeit angelegt.")
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
        messages.success(request, "Tätigkeit gespeichert.")
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
        messages.success(request, f"{form.cleaned_user} wurde der Gruppe hinzugefügt.")
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
    messages.success(request, f"Rolle von {membership.user} geändert.")
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


@login_required
def group_settings(request, group_id):
    """Einstellungen der Gruppe, zurzeit der Abrechnungszyklus (Issue 8)."""
    group = require_group_admin(request.user, group_id)
    form = GroupSettingsForm(request.POST or None, instance=group)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Einstellungen gespeichert.")
        return redirect("groups:settings", group_id=group.pk)

    return render(
        request,
        "groups/group_settings.html",
        {"group": group, "form": form, "period": group.current_period()},
    )


@login_required
def period_list(request, group_id):
    """Abrechnungszeiträume der Gruppe abschließen und wieder öffnen (Issue 5)."""
    group = require_group_read(request.user, group_id)

    if request.method == "POST":
        _handle_period_post(request, group)
        return redirect("groups:periods", group_id=group.pk)

    return render(
        request,
        "groups/period_list.html",
        {
            "group": group,
            "rows": closing.period_overview(group),
            "form": ClosePeriodForm(),
            "may_close": closing.may_close(request.user, group),
            "may_reopen": closing.may_reopen(request.user),
        },
    )


def _handle_period_post(request, group) -> None:
    action = request.POST.get("action")
    try:
        if action == "close":
            form = ClosePeriodForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Der Zeitraum wurde nicht erkannt.")
                return
            period = period_for(form.cleaned_data["period_start"], group.month_start_day)
            closing.close_period(group, period, request.user, form.cleaned_data["note"])
            messages.success(request, f"{period.label} ist abgeschlossen.")
        elif action == "reopen":
            lock_id = request.POST.get("lock") or ""
            if not lock_id.isdigit():
                messages.error(request, "Der Abschluss wurde nicht erkannt.")
                return
            lock = get_object_or_404(PeriodLock, pk=int(lock_id), group=group)
            label = lock.period.label
            closing.reopen_period(lock, request.user)
            messages.success(request, f"{label} ist wieder offen.")
        else:
            messages.error(request, "Unbekannte Aktion.")
    except closing.ClosingError as exc:
        messages.error(request, str(exc))
