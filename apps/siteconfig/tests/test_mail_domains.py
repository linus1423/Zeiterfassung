"""Erlaubte Mail-Domänen und die Auswahl beim Hinzufügen zu einer Gruppe."""

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.groups.models import GroupMembership
from apps.siteconfig.models import MailDomain


def domain_daten(*zeilen, bestehend=()) -> dict:
    """Formulardaten für die Seite "Mail-Domänen"."""
    daten = {
        "domains-TOTAL_FORMS": str(len(bestehend) + len(zeilen)),
        "domains-INITIAL_FORMS": str(len(bestehend)),
        "domains-MIN_NUM_FORMS": "0",
        "domains-MAX_NUM_FORMS": "1000",
    }
    for i, (obj, loeschen) in enumerate(bestehend):
        daten[f"domains-{i}-id"] = str(obj.pk)
        daten[f"domains-{i}-domain"] = obj.domain
        if loeschen:
            daten[f"domains-{i}-DELETE"] = "on"
    for j, wert in enumerate(zeilen, start=len(bestehend)):
        daten[f"domains-{j}-domain"] = wert
    return daten


@pytest.fixture
def superuser(make_user):
    return make_user("root@example.com", is_superuser=True, is_staff=True)


def test_nur_system_admins_pflegen_domaenen(client, group_admin):
    client.force_login(group_admin)

    assert client.get(reverse("siteconfig:mail_domains")).status_code == 403
    response = client.post(reverse("siteconfig:mail_domains"), domain_daten("firma.de"))

    assert response.status_code == 403
    assert not MailDomain.objects.exists()


def test_domaene_wird_normalisiert_gespeichert_und_protokolliert(client, superuser):
    client.force_login(superuser)

    response = client.post(reverse("siteconfig:mail_domains"), domain_daten(" @Firma.DE ", ""))

    assert response.status_code == 302
    assert list(MailDomain.objects.values_list("domain", flat=True)) == ["firma.de"]
    eintrag = AuditLog.objects.get(action=AuditLog.Action.MAIL_DOMAINS_UPDATED)
    assert eintrag.changes == {"hinzugefuegt": ["firma.de"], "entfernt": []}


def test_doppelte_und_ungueltige_domaenen_werden_abgelehnt(client, superuser):
    MailDomain.objects.create(domain="firma.de")
    client.force_login(superuser)

    doppelt = client.post(reverse("siteconfig:mail_domains"), domain_daten("FIRMA.de"))
    kaputt = client.post(reverse("siteconfig:mail_domains"), domain_daten("kein domain"))

    assert doppelt.status_code == 200
    assert kaputt.status_code == 200
    assert MailDomain.objects.count() == 1


def test_domaene_loeschen(client, superuser):
    domain = MailDomain.objects.create(domain="firma.de")
    client.force_login(superuser)

    client.post(reverse("siteconfig:mail_domains"), domain_daten(bestehend=[(domain, True)]))

    assert not MailDomain.objects.exists()
    eintrag = AuditLog.objects.get(action=AuditLog.Action.MAIL_DOMAINS_UPDATED)
    assert eintrag.changes == {"hinzugefuegt": [], "entfernt": ["firma.de"]}


# --- Hinzufügen zu einer Gruppe ------------------------------------------------


def test_ohne_domaene_bleibt_die_ganze_adresse(client, group, group_admin, make_user):
    neu = make_user("neu@example.com")
    client.force_login(group_admin)

    seite = client.get(reverse("groups:members", args=[group.pk])).content.decode()
    assert 'type="email"' in seite
    assert "<select" not in seite.split("Mitglied hinzufügen")[1].split("Rolle")[0]

    client.post(
        reverse("groups:members", args=[group.pk]), {"email": "neu@example.com", "role": "member"}
    )

    assert GroupMembership.objects.filter(group=group, user=neu).exists()


def test_mit_domaene_reicht_der_vordere_teil(client, group, group_admin, make_user):
    MailDomain.objects.create(domain="example.com")
    MailDomain.objects.create(domain="firma.de")
    neu = make_user("Max.Muster@firma.de")
    client.force_login(group_admin)

    seite = client.get(reverse("groups:members", args=[group.pk])).content.decode()
    assert 'name="email_1"' in seite
    assert '<option value="firma.de">firma.de</option>' in seite

    response = client.post(
        reverse("groups:members", args=[group.pk]),
        {"email_0": "max.muster", "email_1": "firma.de", "role": "member"},
    )

    assert response.status_code == 302
    assert GroupMembership.objects.filter(group=group, user=neu).exists()


def test_mit_domaene_geht_auch_eine_fremde_ganze_adresse(client, group, group_admin, make_user):
    MailDomain.objects.create(domain="firma.de")
    extern = make_user("extern@partner.org")
    client.force_login(group_admin)

    client.post(
        reverse("groups:members", args=[group.pk]),
        {"email_0": "extern@partner.org", "email_1": "firma.de", "role": "member"},
    )

    assert GroupMembership.objects.filter(group=group, user=extern).exists()


@pytest.mark.parametrize(
    "eingabe",
    [
        {"email_0": "max", "email_1": "boese.de"},  # Domäne nicht hinterlegt
        {"email_0": "max muster", "email_1": "firma.de"},  # keine gültige Adresse
        {"email_0": "", "email_1": "firma.de"},  # leer
    ],
)
def test_mit_domaene_ungueltige_eingaben(client, group, group_admin, make_user, eingabe):
    MailDomain.objects.create(domain="firma.de")
    make_user("max@boese.de")
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:members", args=[group.pk]), {**eingabe, "role": "member"}
    )

    assert response.status_code == 200
    assert response.context["form"].errors
    assert GroupMembership.objects.filter(group=group).count() == 1


def test_fehlerhafte_eingabe_bleibt_im_formular_stehen(client, group, group_admin):
    MailDomain.objects.create(domain="firma.de")
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:members", args=[group.pk]),
        {"email_0": "niemand", "email_1": "firma.de", "role": "member"},
    )

    seite = response.content.decode()
    assert 'value="niemand"' in seite
    assert '<option value="firma.de" selected>' in seite
    assert "kein aktives Konto" in seite
