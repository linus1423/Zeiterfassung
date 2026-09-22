from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.shortcuts import redirect, render

from apps.audit.models import AuditLog, log

from .forms import FooterLinkFormSet, SiteSettingsForm
from .models import FooterLink, SiteSettings

# Ein Logo ändert sich selten, und die Adresse trägt den Zeitpunkt der letzten
# Änderung (siehe SiteSettings.logo_version). Der Browser darf es deshalb
# lange behalten, ohne ein neues Logo zu verpassen.
LOGO_MAX_AGE = 60 * 60 * 24

# Welches Feld zu welchem Teil der Seite gehört. Das Formular kennt die
# Reihenfolge, die Aufteilung auf die beiden Blöcke steht hier, damit die
# Vorlage sie nicht selbst aufzählen muss.
KOPF_FELDER = ("site_title", "company_name", "logo", "logo_entfernen", "logo_alt_text")
FUSS_FELDER = ("footer_text", "imprint_text", "imprint_url", "privacy_text", "privacy_url")


@login_required
def site_settings(request):
    """Kopf- und Fußzeile pflegen (Issue 77). Nur für System-Admins."""
    if not request.user.is_superuser:
        raise PermissionDenied("Nur System-Admins dürfen die Darstellung ändern.")

    site = SiteSettings.current()
    form = SiteSettingsForm(
        request.POST or None, request.FILES or None, instance=site, prefix="site"
    )
    links = FooterLinkFormSet(
        request.POST or None, queryset=FooterLink.objects.all(), prefix="links"
    )

    if request.method == "POST" and form.is_valid() and links.is_valid():
        gespeichert = form.save()
        links.save()
        log(
            AuditLog.Action.SITE_SETTINGS_UPDATED,
            actor=request.user,
            target=gespeichert,
            changes={"felder": sorted(form.changed_data)},
        )
        messages.success(request, "Die Darstellung ist gespeichert.")
        return redirect("siteconfig:settings")

    return render(
        request,
        "siteconfig/settings.html",
        {
            "form": form,
            "links": links,
            "kopf_felder": KOPF_FELDER,
            "fuss_felder": FUSS_FELDER,
        },
    )


def logo(request):
    """Liefert das hinterlegte Logo aus.

    Ohne Anmeldung erreichbar, weil es auch auf der Anmeldeseite steht. Der
    Medientyp stammt aus der Prüfung beim Hochladen (siehe logo.py), nicht aus
    der Angabe des Browsers.
    """
    site = SiteSettings.current()
    if not site.has_logo:
        raise Http404("Es ist kein Logo hinterlegt.")

    etag = f'"{site.logo_version}"'
    if request.headers.get("If-None-Match") == etag:
        return HttpResponseNotModified()

    response = HttpResponse(bytes(site.logo_data), content_type=site.logo_content_type)
    response["ETag"] = etag
    response["Cache-Control"] = f"public, max-age={LOGO_MAX_AGE}"
    return response


def imprint(request):
    """Impressum, ohne Anmeldung lesbar."""
    site = SiteSettings.current()
    return _text_page(request, "Impressum", site.imprint_text)


def privacy(request):
    """Datenschutzerklärung, ohne Anmeldung lesbar."""
    site = SiteSettings.current()
    return _text_page(request, "Datenschutz", site.privacy_text)


def _text_page(request, title: str, text: str):
    if not text.strip():
        # Kein Text hinterlegt: die Seite gibt es nicht, statt leer zu erscheinen.
        raise Http404(f"Es ist kein Text für {title} hinterlegt.")
    return render(request, "siteconfig/page.html", {"page_title": title, "page_text": text})
