"""Mailversand, der nie den eigentlichen Vorgang kippt.

Benachrichtigungen sind eine Nebensache: ein nicht erreichbarer Mailserver
darf weder eine Fehlerseite erzeugen noch ein Management-Kommando abbrechen.
Deshalb fängt `send_plain_mail` jeden Fehler ab und vermerkt ihn im
Protokoll. Ohne SMTP bleibt es bei den Hinweisen im Tool.

Hier liegen die Bausteine, die Korrekturanträge (Issue 3) und Erinnerungen
(Issue 34) gemeinsam brauchen.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def absolute_url(path: str) -> str:
    """Macht aus einem Pfad einen Link, wenn die Basis-Adresse konfiguriert ist."""
    base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def send_plain_mail(subject: str, template: str, context: dict, recipients: list[str]) -> bool:
    """Verschickt eine Textmail an alle Empfänger. Gibt zurück, ob es geklappt hat."""
    from apps.audit.models import AuditLog, log

    recipients = sorted({address for address in recipients if address})
    if not recipients:
        return False

    body = render_to_string(template, context)
    try:
        connection = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            connection=connection,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Benachrichtigung konnte nicht versandt werden: %s", exc)
        log(AuditLog.Action.NOTIFICATION_FAILED, note=f"{subject}: {exc}")
        return False
    return True
