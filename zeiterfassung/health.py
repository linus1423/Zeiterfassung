"""Endpunkte fuer die Healthchecks der Container.

Docker und Podman pruefen den Zustand eines Containers, indem sie ein Kommando
im Container ausfuehren. Fuer den Webdienst heisst das: einmal selbst anfragen.
Django wuerde eine solche Anfrage normalerweise mit 400 ablehnen, weil
`127.0.0.1` im Betrieb nicht in `DJANGO_ALLOWED_HOSTS` steht, deshalb beantwortet
eine Middleware die beiden Pfade, bevor irgendetwas den Host prueft.
"""

import contextlib
import logging

from django.db import connections
from django.http import HttpResponse

logger = logging.getLogger(__name__)

LIVENESS_PATH = "/healthz"
READINESS_PATH = "/readyz"


class HealthCheckMiddleware:
    """Beantwortet /healthz und /readyz ohne den Rest der Anwendung.

    Die Middleware steht bewusst an erster Stelle: sie ruft `request.get_host()`
    nie auf, wird also nicht von der Hostpruefung abgelehnt, und die
    SecurityMiddleware leitet sie nicht auf HTTPS um.

    /healthz sagt nur, dass der Prozess Anfragen beantwortet. /readyz prueft
    zusaetzlich die Datenbank und ist damit das, was ein Loadbalancer fragen
    sollte. Der Healthcheck des Containers benutzt /healthz, damit eine kurze
    Stoerung der Datenbank nicht den Webdienst neu startet.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == LIVENESS_PATH:
            return _plain("ok")
        if request.path == READINESS_PATH:
            return _readiness()
        return self.get_response(request)


def _plain(text, status=200):
    return HttpResponse(f"{text}\n", content_type="text/plain; charset=utf-8", status=status)


def _readiness():
    connection = connections["default"]
    try:
        # Eine echte Abfrage, nicht nur ensure_connection(): mit CONN_MAX_AGE
        # haelt Django die Verbindung offen, und eine laengst abgerissene
        # Verbindung faellt erst auf, wenn tatsaechlich etwas ueber sie laeuft.
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:
        # Jeder Fehler an dieser Stelle heisst: nicht bereit. Die Ursache
        # gehoert ins Protokoll, nicht in die Antwort.
        logger.warning("Readiness-Pruefung fehlgeschlagen: %s", exc)
        # Die kaputte Verbindung wegwerfen, sonst versucht es die naechste
        # Anfrage wieder mit derselben.
        with contextlib.suppress(Exception):
            connection.close()
        return _plain("database unavailable", status=503)
    return _plain("ok")
