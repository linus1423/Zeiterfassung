"""Die Endpunkte, die Docker und Podman für den Healthcheck anfragen."""

from unittest.mock import patch

import pytest
from django.db import OperationalError


def test_healthz_antwortet_ohne_anmeldung(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.content == b"ok\n"


@pytest.mark.django_db
def test_readyz_antwortet_wenn_die_datenbank_erreichbar_ist(client):
    response = client.get("/readyz")

    assert response.status_code == 200


def test_readyz_meldet_503_wenn_die_abfrage_scheitert(client):
    """Geprüft wird die Abfrage selbst, nicht nur das Oeffnen der Verbindung:
    mit CONN_MAX_AGE hält Django die Verbindung offen, und eine abgerissene
    fällt erst auf, wenn etwas darüber läuft."""
    with patch(
        "django.db.backends.base.base.BaseDatabaseWrapper.cursor",
        side_effect=OperationalError("Verbindung abgerissen"),
    ):
        response = client.get("/readyz")

    assert response.status_code == 503


def test_healthz_ohne_passenden_host(client, settings):
    """Der Healthcheck fragt 127.0.0.1 an, das steht im Betrieb nicht in
    ALLOWED_HOSTS. Die Middleware muss trotzdem antworten, sonst meldet der
    Container sich selbst als kaputt."""
    settings.ALLOWED_HOSTS = ["zeiterfassung.example.com"]

    response = client.get("/healthz", headers={"host": "127.0.0.1:8000"})

    assert response.status_code == 200


def test_healthz_ohne_https_umleitung(client, settings):
    """SECURE_SSL_REDIRECT würde eine Anfrage über http umleiten; der
    Healthcheck spricht aber http auf 127.0.0.1."""
    settings.SECURE_SSL_REDIRECT = True

    response = client.get("/healthz")

    assert response.status_code == 200


def test_andere_pfade_laufen_weiter_durch(client):
    response = client.get("/")

    assert response.status_code in (302, 200)
