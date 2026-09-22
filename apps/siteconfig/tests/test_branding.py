"""Kopf- und Fußzeile mit konfigurierbaren Freitextfeldern (Issue 77)."""

import base64

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.siteconfig.logo import MAX_LOGO_BYTES
from apps.siteconfig.models import FooterLink, SiteSettings

# Ein gültiges PNG mit einem einzigen durchsichtigen Pixel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def feldwerte(**felder) -> dict:
    """Formulardaten für die Seite "Darstellung", inklusive leerem Formularsatz."""
    daten = {
        "site-site_title": "Zeiterfassung",
        "site-company_name": "",
        "site-logo_alt_text": "",
        "site-footer_text": "",
        "site-imprint_text": "",
        "site-imprint_url": "",
        "site-privacy_text": "",
        "site-privacy_url": "",
        "links-TOTAL_FORMS": "0",
        "links-INITIAL_FORMS": "0",
        "links-MIN_NUM_FORMS": "0",
        "links-MAX_NUM_FORMS": "1000",
    }
    daten.update({f"site-{name}": wert for name, wert in felder.items()})
    return daten


def test_ohne_pflege_bleibt_die_vorgabe(client, member):
    client.force_login(member)

    response = client.get(reverse("tracking:clock"))

    inhalt = response.content.decode()
    assert "Zeiterfassung" in inhalt
    # Gelesen wird ohne zu schreiben: die Zeile entsteht erst beim Speichern.
    assert not SiteSettings.objects.exists()


def test_kopf_und_fusszeile_zeigen_die_gepflegten_werte(client, member):
    SiteSettings(
        site_title="Zeiten",
        company_name="Muster GmbH",
        footer_text="Musterweg 1\n12345 Musterstadt",
    ).save()
    client.force_login(member)

    inhalt = client.get(reverse("tracking:clock")).content.decode()

    assert "Zeiten" in inhalt
    assert "Muster GmbH" in inhalt
    assert "Musterweg 1" in inhalt


def test_fusszeile_verweist_auf_die_eigene_impressumsseite(client, member):
    SiteSettings(imprint_text="Muster GmbH, Musterweg 1").save()
    client.force_login(member)

    inhalt = client.get(reverse("tracking:clock")).content.decode()

    assert reverse("siteconfig:imprint") in inhalt


def test_eine_hinterlegte_adresse_geht_der_eigenen_seite_vor(client, member):
    SiteSettings(imprint_text="Muster GmbH", imprint_url="https://example.org/impressum").save()
    client.force_login(member)

    inhalt = client.get(reverse("tracking:clock")).content.decode()

    assert "https://example.org/impressum" in inhalt


def test_impressum_ist_ohne_anmeldung_lesbar(client, db):
    SiteSettings(imprint_text="Muster GmbH, Musterweg 1").save()

    response = client.get(reverse("siteconfig:imprint"))

    assert response.status_code == 200
    assert "Musterweg 1" in response.content.decode()


def test_datenschutz_ohne_text_gibt_es_nicht(client, db):
    response = client.get(reverse("siteconfig:privacy"))

    assert response.status_code == 404


def test_darstellung_pflegt_nur_der_system_admin(client, group_admin):
    client.force_login(group_admin)

    assert client.get(reverse("siteconfig:settings")).status_code == 403


def test_darstellung_speichern(client, superuser):
    client.force_login(superuser)

    client.post(
        reverse("siteconfig:settings"),
        feldwerte(
            site_title="Zeiten",
            company_name="Muster GmbH",
            footer_text="Musterweg 1",
            privacy_text="Wir speichern Arbeitszeiten.",
        ),
        follow=True,
    )

    site = SiteSettings.objects.get()
    assert site.site_title == "Zeiten"
    assert site.company_name == "Muster GmbH"
    assert site.privacy_link == reverse("siteconfig:privacy")
    assert AuditLog.objects.filter(action=AuditLog.Action.SITE_SETTINGS_UPDATED).exists()


def test_zweites_speichern_legt_keine_zweite_zeile_an(client, superuser):
    client.force_login(superuser)

    client.post(reverse("siteconfig:settings"), feldwerte(company_name="Eins"))
    client.post(reverse("siteconfig:settings"), feldwerte(company_name="Zwei"))

    assert SiteSettings.objects.count() == 1
    assert SiteSettings.objects.get().company_name == "Zwei"


@pytest.mark.parametrize("feld", ["imprint_url", "privacy_url"])
def test_adressen_ohne_web_schema_werden_abgelehnt(client, superuser, feld):
    client.force_login(superuser)

    response = client.post(
        reverse("siteconfig:settings"), feldwerte(**{feld: "javascript:alert(1)"})
    )

    assert response.status_code == 200
    assert not SiteSettings.objects.exists()


def test_logo_hochladen_und_ausliefern(client, superuser):
    client.force_login(superuser)
    daten = feldwerte(company_name="Muster GmbH")
    daten["site-logo"] = SimpleUploadedFile("logo.png", PNG, content_type="image/png")

    client.post(reverse("siteconfig:settings"), daten, follow=True)

    site = SiteSettings.objects.get()
    assert site.has_logo
    assert site.logo_content_type == "image/png"

    response = client.get(reverse("siteconfig:logo"))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == PNG


def test_logo_wird_nur_bei_aenderung_neu_geholt(client, superuser):
    client.force_login(superuser)
    daten = feldwerte()
    daten["site-logo"] = SimpleUploadedFile("logo.png", PNG, content_type="image/png")
    client.post(reverse("siteconfig:settings"), daten, follow=True)

    erste = client.get(reverse("siteconfig:logo"))
    zweite = client.get(reverse("siteconfig:logo"), headers={"if-none-match": erste["ETag"]})

    assert zweite.status_code == 304


def test_ohne_logo_gibt_es_keines(client, db):
    assert client.get(reverse("siteconfig:logo")).status_code == 404


def test_logo_entfernen(client, superuser):
    SiteSettings(logo_data=PNG, logo_content_type="image/png", logo_filename="logo.png").save()
    client.force_login(superuser)

    client.post(reverse("siteconfig:settings"), feldwerte(logo_entfernen="on"), follow=True)

    assert not SiteSettings.objects.get().has_logo
    assert client.get(reverse("siteconfig:logo")).status_code == 404


def test_eine_datei_die_kein_bild_ist_wird_abgelehnt(client, superuser):
    client.force_login(superuser)
    daten = feldwerte()
    # Als Bild ausgegeben, tatsächlich ein Dokument mit Skript: genau der Fall,
    # den die Prüfung des Inhalts abfangen soll.
    daten["site-logo"] = SimpleUploadedFile(
        "logo.svg",
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        content_type="image/png",
    )

    response = client.post(reverse("siteconfig:settings"), daten)

    assert response.status_code == 200
    assert not SiteSettings.objects.exists()


def test_ein_zu_grosses_logo_wird_abgelehnt(client, superuser):
    client.force_login(superuser)
    daten = feldwerte()
    daten["site-logo"] = SimpleUploadedFile(
        "gross.png", PNG + b"\x00" * MAX_LOGO_BYTES, content_type="image/png"
    )

    response = client.post(reverse("siteconfig:settings"), daten)

    assert response.status_code == 200
    assert not SiteSettings.objects.exists()


def test_weitere_verweise_stehen_in_der_fusszeile(client, superuser, member):
    client.force_login(superuser)
    daten = feldwerte()
    daten.update(
        {
            "links-TOTAL_FORMS": "2",
            "links-0-label": "Kontakt",
            "links-0-url": "https://example.org/kontakt",
            "links-0-position": "1",
            "links-1-label": "",
            "links-1-url": "",
            "links-1-position": "0",
        }
    )

    client.post(reverse("siteconfig:settings"), daten, follow=True)

    assert [link.label for link in FooterLink.objects.all()] == ["Kontakt"]

    client.force_login(member)
    inhalt = client.get(reverse("tracking:clock")).content.decode()
    assert "https://example.org/kontakt" in inhalt


def test_der_system_admin_kommt_aus_der_navigation_zur_pflege(client, superuser):
    client.force_login(superuser)

    inhalt = client.get(reverse("tracking:clock")).content.decode()

    assert reverse("siteconfig:settings") in inhalt
