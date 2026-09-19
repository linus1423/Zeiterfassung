from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.groups import closing
from apps.tracking.models import TimeEntry

from . import services
from .forms import (
    CorrectionRequestForm,
    DecisionForm,
    DeleteRequestForm,
    break_formset,
    validate_breaks_within,
)
from .models import CorrectionRequest


@login_required
def my_requests(request):
    requests = list(
        CorrectionRequest.objects.filter(requested_by=request.user)
        .select_related("group", "time_entry", "proposed_activity", "decided_by")
        .order_by("-created_at")
    )
    # Wer seine Antraege ansieht, hat die Entscheidungen gesehen; damit geht
    # der Zaehler in der Navigation wieder aus.
    services.mark_decisions_seen(request.user)
    return render(request, "corrections/my_requests.html", {"requests": requests})


@login_required
def request_create(request, entry_id=None):
    """Aenderung eines bestehenden Eintrags oder Nachtrag eines vergessenen."""
    entry = None
    if entry_id is not None:
        entry = get_object_or_404(
            TimeEntry.objects.select_related("group", "activity").prefetch_related("breaks"),
            pk=entry_id,
        )
        if entry.user_id != request.user.pk:
            raise PermissionDenied("Du kannst nur eigene Zeiten korrigieren lassen.")
        if entry.is_open:
            messages.error(request, "Ein laufender Eintrag kann nicht korrigiert werden.")
            return redirect("tracking:clock")

    posted = request.POST if request.method == "POST" else None
    form = CorrectionRequestForm(request.user, posted, entry=entry)
    breaks = break_formset(posted, entry=entry)

    if request.method == "POST":
        valid = form.is_valid() and breaks.is_valid()
        if valid:
            valid = validate_breaks_within(
                breaks, form.cleaned_data["start"], form.cleaned_data["end"]
            )
        if valid:
            try:
                services.create_request(
                    requested_by=request.user,
                    group=form.cleaned_data["group"],
                    kind=CorrectionRequest.Kind.EDIT if entry else CorrectionRequest.Kind.CREATE,
                    reason=form.cleaned_data["reason"],
                    entry=entry,
                    proposed_start=form.cleaned_data["start"],
                    proposed_end=form.cleaned_data["end"],
                    proposed_activity=form.cleaned_data.get("activity"),
                    proposed_breaks=breaks.breaks(),
                )
            except services.CorrectionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request, "Antrag gestellt. Ein Admin der Gruppe entscheidet darueber."
                )
                return redirect("corrections:mine")

    return render(
        request,
        "corrections/request_form.html",
        {"form": form, "breaks": breaks, "entry": entry},
    )


@login_required
def request_delete(request, entry_id):
    """Antrag, einen Eintrag ganz zu entfernen."""
    entry = get_object_or_404(TimeEntry.objects.select_related("group"), pk=entry_id)
    if entry.user_id != request.user.pk:
        raise PermissionDenied("Du kannst nur eigene Zeiten korrigieren lassen.")

    lock = closing.lock_for(entry.group, timezone.localtime(entry.start).date())
    if lock is not None:
        messages.error(
            request,
            f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
            "Korrekturen sind dort nicht mehr moeglich.",
        )
        return redirect("tracking:my_entries")

    form = DeleteRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_request(
                requested_by=request.user,
                group=entry.group,
                kind=CorrectionRequest.Kind.DELETE,
                reason=form.cleaned_data["reason"],
                entry=entry,
            )
        except services.CorrectionError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Antrag auf Loeschung gestellt.")
            return redirect("corrections:mine")

    return render(request, "corrections/delete_form.html", {"form": form, "entry": entry})


@require_POST
@login_required
def request_withdraw(request, request_id):
    correction = get_object_or_404(CorrectionRequest, pk=request_id)
    try:
        services.withdraw(correction, request.user)
        messages.success(request, "Antrag zurueckgenommen.")
    except services.CorrectionError as exc:
        messages.error(request, str(exc))
    return redirect("corrections:mine")


@login_required
def inbox(request):
    """Offene Antraege der Gruppen, in denen der Nutzer Admin ist."""
    group_ids = request.user.admin_group_ids()
    requests = (
        CorrectionRequest.objects.filter(
            group_id__in=group_ids, status=CorrectionRequest.Status.PENDING
        )
        .select_related("group", "requested_by", "time_entry", "proposed_activity")
        .order_by("created_at")
    )
    decided = (
        CorrectionRequest.objects.filter(group_id__in=group_ids)
        .exclude(status=CorrectionRequest.Status.PENDING)
        .select_related("group", "requested_by", "decided_by")
        .order_by("-decided_at")[:20]
    )
    return render(request, "corrections/inbox.html", {"requests": requests, "decided": decided})


@login_required
def decide(request, request_id):
    """Einen Antrag ansehen und entscheiden."""
    correction = get_object_or_404(
        CorrectionRequest.objects.select_related(
            "group", "requested_by", "time_entry", "proposed_activity"
        ),
        pk=request_id,
    )
    if not request.user.is_group_admin(correction.group):
        raise PermissionDenied("Nur Admins dieser Gruppe duerfen Antraege entscheiden.")

    form = DecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        note = form.cleaned_data["note"]
        action = request.POST.get("action")
        try:
            if action == "approve":
                services.approve(correction, request.user, note)
                messages.success(request, "Antrag genehmigt, die Zeit wurde geaendert.")
            elif action == "reject":
                services.reject(correction, request.user, note)
                messages.success(request, "Antrag abgelehnt.")
            else:
                messages.error(request, "Unbekannte Aktion.")
                return redirect("corrections:decide", request_id=correction.pk)
            return redirect("corrections:inbox")
        except services.CorrectionError as exc:
            messages.error(request, str(exc))

    return render(
        request,
        "corrections/decide.html",
        {
            "correction": correction,
            "form": form,
            "may_decide": services.may_decide(request.user, correction),
            "period_lock": services.closed_period_lock(correction),
        },
    )
