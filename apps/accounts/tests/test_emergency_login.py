"""Notfallzugang für System-Admins (Issue 51).

Der Zugang ist die einzige Ausnahme von "Anmeldung nur über OIDC". Getestet
wird deshalb vor allem, was er *nicht* darf: ausgeschaltet nicht existieren,
niemanden ohne System-Admin-Rolle hereinlassen und nichts Geheimes ins
Protokoll schreiben.
"""

import importlib
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.test import Client, RequestFactory, override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse

from apps.accounts import urls as accounts_urls
from apps.accounts import views
from apps.audit.models import AuditLog

PASSWORT = "test-passwort-1234"
NOTFALL_PFAD = "/konto/notfall-anmeldung/"


def _reload_urls() -> None:
    """Die URL-Muster neu aufbauen.

    Ob es den Notfallzugang gibt, entscheidet sich beim Import von
    apps/accounts/urls.py. Für einen Test, der den Schalter umlegt, müssen die
    Muster deshalb neu gelesen werden — genau wie bei einem Neustart.
    """
    importlib.reload(accounts_urls)
    importlib.reload(importlib.import_module(settings.ROOT_URLCONF))
    clear_url_caches()


@contextmanager
def notfallzugang_an(**extra):
    """Der Notfallzugang ist innerhalb dieses Blocks eingeschaltet."""
    overridden = override_settings(EMERGENCY_LOGIN_ENABLED=True, **extra)
    overridden.enable()
    _reload_urls()
    cache.clear()
    try:
        yield
    finally:
        overridden.disable()
        _reload_urls()
        cache.clear()


def _anmelden(client, benutzername, passwort=PASSWORT, **extra):
    return client.post(
        reverse("accounts:emergency_login"),
        {"username": benutzername, "password": passwort},
        **extra,
    )


# --- ausgeschaltet ---------------------------------------------------------


def test_ausgeschaltet_gibt_es_die_url_nicht(client, db):
    assert settings.EMERGENCY_LOGIN_ENABLED is False

    with pytest.raises(NoReverseMatch):
        reverse("accounts:emergency_login")

    assert client.get(NOTFALL_PFAD).status_code == 404
    assert (
        client.post(NOTFALL_PFAD, {"username": "system", "password": PASSWORT}).status_code == 404
    )


def test_ausgeschaltet_steht_nichts_auf_der_anmeldeseite(client, db):
    body = client.get(reverse("accounts:login")).content.decode()

    assert "Notfallzugang" not in body


def test_view_bleibt_verschlossen_wenn_sie_jemand_direkt_einhaengt(db):
    """Zweite Reihe: auch direkt aufgerufen gibt die View ohne Schalter nichts her."""
    request = RequestFactory().get(NOTFALL_PFAD)

    with pytest.raises(Http404):
        views.emergency_login(request)


# --- eingeschaltet ---------------------------------------------------------


def test_eingeschaltet_steht_der_hinweis_auf_der_anmeldeseite(client, db):
    with notfallzugang_an():
        body = client.get(reverse("accounts:login")).content.decode()

    assert "Notfallzugang für System-Admins" in body
    assert "Identity-Provider" in body
    assert NOTFALL_PFAD in body


def test_formular_gibt_das_passwort_nicht_zurueck(client, superuser):
    with notfallzugang_an():
        response = _anmelden(client, superuser.username, "falsch-aber-lang")

    assert response.status_code == 200
    assert "falsch-aber-lang" not in response.content.decode()


def test_superuser_kommt_mit_passwort_hinein(client, superuser):
    with notfallzugang_an():
        response = _anmelden(client, superuser.username)

        assert response.status_code == 302
        assert response["Location"] == reverse("admin:index")
        assert client.session["_auth_user_id"] == str(superuser.pk)

    eintrag = AuditLog.objects.get(action=AuditLog.Action.EMERGENCY_LOGIN)
    assert eintrag.actor_id == superuser.pk
    assert eintrag.changes["benutzername"] == superuser.username
    assert PASSWORT not in str(eintrag.changes) + eintrag.note


def test_superuser_ohne_adminrecht_landet_in_der_anwendung(client, make_user):
    person = make_user("nurroot@example.com", is_superuser=True, is_staff=False)

    with notfallzugang_an():
        response = _anmelden(client, person.username)

        assert response["Location"] == reverse("tracking:clock")
        assert client.session["_auth_user_id"] == str(person.pk)


def test_nichtsuperuser_kommt_auch_mit_richtigem_passwort_nicht_hinein(client, member):
    with notfallzugang_an():
        response = _anmelden(client, member.username)

        assert response.status_code == 200
        assert "_auth_user_id" not in client.session

    eintrag = AuditLog.objects.get(action=AuditLog.Action.EMERGENCY_LOGIN_FAILED)
    assert eintrag.actor_id is None
    assert eintrag.subject_id == member.pk
    assert eintrag.note.startswith("Kein System-Admin")
    assert PASSWORT not in str(eintrag.changes) + eintrag.note


def test_gruppenadmin_und_buchhaltung_kommen_ebenfalls_nicht_hinein(
    client, group_admin, accountant
):
    with notfallzugang_an():
        for person in (group_admin, accountant):
            assert _anmelden(client, person.username).status_code == 200
            assert "_auth_user_id" not in client.session

    assert AuditLog.objects.filter(action=AuditLog.Action.EMERGENCY_LOGIN_FAILED).count() == 2


def test_falsches_passwort_wird_protokolliert(client, superuser):
    with notfallzugang_an():
        response = _anmelden(client, superuser.username, "ganz-falsch")

        assert response.status_code == 200
        assert "_auth_user_id" not in client.session

    eintrag = AuditLog.objects.get(action=AuditLog.Action.EMERGENCY_LOGIN_FAILED)
    assert eintrag.changes["benutzername"] == superuser.username
    assert "ganz-falsch" not in str(eintrag.changes) + eintrag.note


def test_unbekannter_benutzername_wird_protokolliert(client, db):
    with notfallzugang_an():
        response = _anmelden(client, "gibtsnicht")

    assert response.status_code == 200
    eintrag = AuditLog.objects.get(action=AuditLog.Action.EMERGENCY_LOGIN_FAILED)
    assert eintrag.subject_id is None
    assert eintrag.changes["benutzername"] == "gibtsnicht"


def test_die_meldung_verraet_nicht_woran_es_lag(client, superuser, member):
    with notfallzugang_an():
        falsches_passwort = _anmelden(client, superuser.username, "ganz-falsch")
        keine_rolle = _anmelden(client, member.username)
        unbekannt = _anmelden(client, "gibtsnicht")

    for antwort in (falsches_passwort, keine_rolle, unbekannt):
        assert views.EMERGENCY_ERROR in antwort.content.decode()


def test_inaktiver_superuser_kommt_nicht_hinein(client, superuser):
    superuser.is_active = False
    superuser.save(update_fields=["is_active"])

    with notfallzugang_an():
        response = _anmelden(client, superuser.username)

        assert response.status_code == 200
        assert "_auth_user_id" not in client.session


def test_konto_aus_dem_identity_provider_hat_kein_brauchbares_passwort(client, superuser):
    """Wer über OIDC angelegt wurde, hat kein Passwort — und kommt so nicht rein."""
    superuser.set_unusable_password()
    superuser.save(update_fields=["password"])

    with notfallzugang_an():
        for versuch in ("", "!", PASSWORT):
            assert "_auth_user_id" not in client.session
            _anmelden(client, superuser.username, versuch)

        assert "_auth_user_id" not in client.session


def test_bereits_angemeldet_wird_weitergeleitet(client, member):
    client.force_login(member)

    with notfallzugang_an():
        response = client.get(reverse("accounts:emergency_login"))

    assert response.status_code == 302
    assert response["Location"] == reverse("tracking:clock")


def test_ohne_csrf_token_geht_nichts(superuser):
    streng = Client(enforce_csrf_checks=True)

    with notfallzugang_an():
        response = _anmelden(streng, superuser.username)

        assert response.status_code == 403
        assert "_auth_user_id" not in streng.session


# --- Begrenzung der Versuche ----------------------------------------------


def test_zu_viele_fehlversuche_sperren_auch_das_richtige_passwort(client, superuser):
    with notfallzugang_an(EMERGENCY_LOGIN_MAX_ATTEMPTS=2):
        _anmelden(client, superuser.username, "falsch-1")
        _anmelden(client, superuser.username, "falsch-2")

        gesperrt = _anmelden(client, superuser.username)

        assert gesperrt.status_code == 200
        assert "_auth_user_id" not in client.session
        assert views.EMERGENCY_BLOCKED in gesperrt.content.decode()

    # Während der Sperre wird nicht weiter protokolliert, sonst wäre das
    # Durchprobieren ein billiger Weg, die Datenbank vollzuschreiben.
    assert AuditLog.objects.filter(action=AuditLog.Action.EMERGENCY_LOGIN_FAILED).count() == 2


def test_die_sperre_greift_auch_von_einer_anderen_adresse(client, superuser):
    with notfallzugang_an(EMERGENCY_LOGIN_MAX_ATTEMPTS=1):
        _anmelden(client, superuser.username, "falsch", REMOTE_ADDR="10.0.0.1")

        gesperrt = _anmelden(client, superuser.username, PASSWORT, REMOTE_ADDR="10.0.0.2")

        assert "_auth_user_id" not in client.session
        assert views.EMERGENCY_BLOCKED in gesperrt.content.decode()


def test_die_sperre_greift_auch_fuer_einen_anderen_benutzernamen(client, superuser):
    with notfallzugang_an(EMERGENCY_LOGIN_MAX_ATTEMPTS=1):
        _anmelden(client, "gibtsnicht", "falsch")

        gesperrt = _anmelden(client, superuser.username)

        assert "_auth_user_id" not in client.session
        assert views.EMERGENCY_BLOCKED in gesperrt.content.decode()


def test_eine_erfolgreiche_anmeldung_setzt_den_zaehler_zurueck(client, superuser):
    with notfallzugang_an(EMERGENCY_LOGIN_MAX_ATTEMPTS=3):
        _anmelden(client, superuser.username, "falsch-1")
        _anmelden(client, superuser.username, "falsch-2")

        assert _anmelden(client, superuser.username).status_code == 302

        client.logout()
        assert _anmelden(client, superuser.username, "falsch-3").status_code == 200
        assert _anmelden(client, superuser.username).status_code == 302


def test_x_forwarded_for_zaehlt_nur_hinter_einem_eigenen_proxy(client, superuser):
    """Ohne DJANGO_BEHIND_PROXY darf ein erfundener Header die Sperre nicht umgehen."""
    with notfallzugang_an(EMERGENCY_LOGIN_MAX_ATTEMPTS=1, BEHIND_PROXY=False):
        _anmelden(client, superuser.username, "falsch", HTTP_X_FORWARDED_FOR="1.2.3.4")

        gesperrt = _anmelden(client, superuser.username, PASSWORT, HTTP_X_FORWARDED_FOR="5.6.7.8")

        assert views.EMERGENCY_BLOCKED in gesperrt.content.decode()


def test_hinter_einem_proxy_zaehlt_der_letzte_eintrag(client, superuser):
    with notfallzugang_an(EMERGENCY_LOGIN_MAX_ATTEMPTS=1, BEHIND_PROXY=True):
        # Der vom Client erfundene Teil steht links, der echte Absender rechts.
        _anmelden(client, "gibtsnicht", "falsch", HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.1")

        gesperrt = _anmelden(
            client, superuser.username, PASSWORT, HTTP_X_FORWARDED_FOR="8.8.8.8, 10.0.0.1"
        )

        assert "_auth_user_id" not in client.session
        assert views.EMERGENCY_BLOCKED in gesperrt.content.decode()
