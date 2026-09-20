"""Lesende Sicht auf das Änderungsprotokoll (Issue 33).

Wer für die Richtigkeit der Zeiten geradesteht, muss nachsehen können, wer
eine Zeit wann und mit welcher Begründung geändert hat. Bisher kam nur ein
System-Admin über die Django-Adminoberfläche daran.

Gelesen wird ausschließlich: das Protokoll wird geschrieben, nie geändert.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from apps.groups.permissions import require_group_read
from apps.tracking.models import TimeEntry
from apps.tracking.utils import day_bounds

from .forms import AuditFilterForm
from .models import AuditLog
from .summary import describe_changes

PER_PAGE = 50


def _people_in_log(group) -> list:
    """Die Personen, die im Protokoll dieser Gruppe vorkommen.

    Nicht die Mitgliederliste: es handeln auch System-Admins, und
    ausgeschiedene Mitglieder stehen weiter im Protokoll.
    """
    rows = AuditLog.objects.filter(group=group)
    ids = set(rows.values_list("actor_id", flat=True).distinct())
    ids |= set(rows.values_list("subject_id", flat=True).distinct())
    ids.discard(None)
    return list(get_user_model().objects.filter(pk__in=ids))


def _with_details(rows) -> list:
    for row in rows:
        row.details = describe_changes(row.changes)
    return rows


@login_required
def group_log(request, group_id):
    """Das Protokoll einer Gruppe, gefiltert und seitenweise."""
    group = require_group_read(request.user, group_id)

    form = AuditFilterForm(_people_in_log(group), request.GET or None)
    # Einträge ohne Gruppe, etwa Exporte, gehören in keine Gruppenansicht.
    queryset = AuditLog.objects.filter(group=group).select_related("actor", "subject")

    if form.is_valid():
        person_id = form.person_id
        if person_id is not None:
            queryset = queryset.filter(Q(actor_id=person_id) | Q(subject_id=person_id))
        if form.cleaned_data.get("action"):
            queryset = queryset.filter(action=form.cleaned_data["action"])
        if form.cleaned_data.get("start"):
            queryset = queryset.filter(created_at__gte=day_bounds(form.cleaned_data["start"])[0])
        if form.cleaned_data.get("end"):
            queryset = queryset.filter(created_at__lt=day_bounds(form.cleaned_data["end"])[1])

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("seite"))
    rows = _with_details(list(page.object_list))

    # Für die Blätterlinks: die Filter bleiben erhalten, die Seite nicht.
    query = request.GET.copy()
    query.pop("seite", None)

    return render(
        request,
        "audit/group_log.html",
        {"group": group, "form": form, "page": page, "rows": rows, "query": query.urlencode()},
    )


@login_required
def entry_log(request, entry_id):
    """Die Geschichte eines einzelnen Zeiteintrags, samt seiner Anträge."""
    entry = get_object_or_404(
        TimeEntry.objects.select_related("user", "group", "activity"), pk=entry_id
    )
    group = require_group_read(request.user, entry.group_id)

    request_ids = list(entry.correction_requests.values_list("pk", flat=True))
    rows = list(
        AuditLog.objects.filter(
            Q(target_type="TimeEntry", target_id=entry.pk)
            | Q(target_type="CorrectionRequest", target_id__in=request_ids)
        )
        .select_related("actor", "subject")
        .order_by("-created_at")
    )

    return render(
        request,
        "audit/entry_log.html",
        {"group": group, "entry": entry, "rows": _with_details(rows)},
    )
