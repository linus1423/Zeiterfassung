"""Oberfläche für den CSV-Import von Zeiten (Issue 54).

Bewusst in einer eigenen Datei und nicht in views.py: dort steht die
Stempeluhr, die jeder benutzt, hier eine Seite für System-Admins.

Zwei Schritte: das Hochladen prüft nur und zeigt das Ergebnis, erst der
zweite Knopf schreibt. Geprüft wird dabei beide Male, denn zwischen Vorschau
und Übernahme kann sich der Bestand geändert haben.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.groups.permissions import require_system_admin

from . import csv_import
from .models import EntryImport

NO_ACCESS = "Nur System-Admins dürfen Zeiten importieren."

# So viele geplante Zeilen zeigt die Vorschau. Mehr hilft niemandem, die
# Zusammenfassung darüber sagt, wie viele es insgesamt sind.
PREVIEW_ROWS = 50


class ImportUploadForm(forms.Form):
    """Die hochgeladene Datei. Größe und Endung werden hier schon begrenzt."""

    datei = forms.FileField(
        label="CSV-Datei",
        help_text=(
            "Deutsches CSV: Semikolon als Trennzeichen, UTF-8. "
            f"Höchstens {csv_import.MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
            f"und {csv_import.MAX_ROWS} Zeilen."
        ),
    )

    def clean_datei(self):
        upload = self.cleaned_data["datei"]
        if upload.size > csv_import.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"Die Datei ist größer als {csv_import.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            )
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise forms.ValidationError("Bitte eine Datei mit der Endung .csv hochladen.")
        return upload


def _render(request, form, plan=None, record=None):
    return render(
        request,
        "tracking/import.html",
        {
            "form": form,
            "plan": plan,
            "record": record,
            "preview_rows": plan.entries[:PREVIEW_ROWS] if plan else [],
            "preview_limit": PREVIEW_ROWS,
        },
    )


@login_required
def import_entries(request):
    """Datei hochladen und das Ergebnis der Prüfung ansehen."""
    require_system_admin(request.user, NO_ACCESS)

    form = ImportUploadForm(request.POST or None, request.FILES or None)
    if request.method != "POST" or not form.is_valid():
        return _render(request, form)

    upload = form.cleaned_data["datei"]
    upload.seek(0)
    # Mehr als erlaubt wird gar nicht erst gelesen; decode() lehnt es dann ab.
    data = upload.read(csv_import.MAX_UPLOAD_BYTES + 1)
    try:
        plan = csv_import.prepare(data)
    except csv_import.CsvImportError as exc:
        messages.error(request, str(exc))
        return _render(request, ImportUploadForm())

    # Alte, nie übernommene Vormerkungen liegen sonst ewig herum.
    csv_import.purge_stale()
    record = EntryImport.objects.create(
        created_by=request.user,
        filename=upload.name[:255],
        payload=data,
        summary=plan.summary(),
    )
    if plan.ok:
        messages.success(
            request,
            f"{len(plan.entries)} Zeiten geprüft und bereit. Es wurde noch nichts geschrieben.",
        )
    else:
        messages.error(
            request,
            f"{plan.error_count} Zeilen sind fehlerhaft. "
            "Solange ein Fehler bleibt, wird nichts importiert.",
        )
    return _render(request, ImportUploadForm(), plan, record)


@require_POST
@login_required
def import_apply(request, import_id):
    """Den zweiten Schritt gehen und die geprüfte Datei wirklich schreiben."""
    require_system_admin(request.user, NO_ACCESS)

    # Nur die eigene Vormerkung: eine fremde könnte längst überholt sein.
    record = get_object_or_404(
        EntryImport,
        pk=import_id,
        created_by=request.user,
        status=EntryImport.Status.PREPARED,
    )
    try:
        result = csv_import.apply_record(record, actor=request.user)
    except csv_import.CsvImportError as exc:
        messages.error(request, str(exc))
        return _render(request, ImportUploadForm(), exc.plan, record)

    messages.success(
        request,
        f"{result.count} Zeiten importiert, {result.plan.total_hours} Stunden.",
    )
    return redirect("tracking:import_entries")


@login_required
def import_sample(request):
    """Die Beispieldatei mit Kopfzeile zum Herunterladen."""
    require_system_admin(request.user, NO_ACCESS)

    response = HttpResponse(csv_import.sample_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{csv_import.SAMPLE_FILENAME}"'
    return response
