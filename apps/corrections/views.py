from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditLog, log
from apps.tracking.models import TimeEntry

from . import services
from .forms import CorrectionRequestForm, DecisionForm, DeleteRequestForm
from .models import CorrectionRequest


@login_required
def my_requests(request):
    requests = (
        CorrectionRequest.objects.filter(requested_by=request.user)
        .select_related("group", "time_entry", "proposed_activity", "decided_by")
        .order_by("-created_at")
    )
    return render(request, "corrections/my_requests.html", {"requests": requests})


@login_required
def request_create(request, entry_id=None):
    """Aenderung eines bestehenden Eintrags oder Nachtrag eines vergessenen."""
    entry = None
    if entry_id is not None:
        entry = get_object_or_404(
            TimeEntry.objects.select_related("group", "activity"), pk=entry_id
        )
        if entry.user_id != request.user.pk:
            raise PermissionDenied("Du kannst nur eigene Zeiten korrigieren lassen.")
        if entry.is_open:
            messages.error(request, "Ein laufender Eintrag kann nicht korrigiert werden.")
            return redirect("tracking:clock")

    form = CorrectionRequestForm(request.user, request.POST or None, entry=entry)

    if request.method == "POST" and form.is_valid():
        correction = CorrectionRequest.objects.create(
            time_entry=entry,
            requested_by=request.user,
            group=form.cleaned_data["group"],
            kind=CorrectionRequest.Kind.EDIT if entry else CorrectionRequest.Kind.CREATE,
            proposed_start=form.cleaned_data["start"],
            proposed_end=form.cleaned_data["end"],
            proposed_activity=form.cleaned_data.get("activity"),
            proposed_breaks=form.proposed_breaks(),
            reason=form.cleaned_data["reason"],
        )
        log(
            AuditLog.Action.CORRECTION_REQUESTED,
            actor=request.user,
            target=correction,
            group=correction.group,
            subject=request.user,
        )
        messages.success(request, "Antrag gestellt. Ein Admin der Gruppe entscheidet darueber.")
        return redirect("corrections:mine")

    return render(request, "corrections/request_form.html", {"form": form, "entry": entry})


@login_required
def request_delete(request, entry_id):
    """Antrag, einen Eintrag ganz zu entfernen."""
    entry = get_object_or_404(TimeEntry.objects.select_related("group"), pk=entry_id)
    if entry.user_id != request.user.pk:
        raise PermissionDenied("Du kannst nur eigene Zeiten korrigieren lassen.")

    form = DeleteRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        correction = CorrectionRequest.objects.create(
            time_entry=entry,
            requested_by=request.user,
            group=entry.group,
            kind=CorrectionRequest.Kind.DELETE,
            reason=form.cleaned_data["reason"],
        )
        log(
            AuditLog.Action.CORRECTION_REQUESTED,
            actor=request.user,
            target=correction,
            group=correction.group,
            subject=request.user,
        )
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
        },
    )
