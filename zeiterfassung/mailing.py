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
from django.core.mail import EmailMessage, get_connection, send_mail
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


def send_mail_with_attachment(
    subject: str,
    template: str,
    context: dict,
    recipients: list[str],
    *,
    filename: str,
    content: bytes,
    mimetype: str,
) -> tuple[bool, str]:
    """Verschickt eine Textmail mit genau einem Anhang.

    Gibt zurück, ob es geklappt hat, und im Fehlerfall den Grund im Klartext.
    Wie `send_plain_mail` fliegt hier nichts nach oben: ein geplanter Export
    darf die übrigen Pläne nicht mitreißen.
    """
    from apps.audit.models import AuditLog, log

    recipients = sorted({address for address in recipients if address})
    if not recipients:
        return False, "Es ist kein Empfänger hinterlegt."

    body = render_to_string(template, context)
    try:
        connection = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
            connection=connection,
        )
        message.attach(filename, content, mimetype)
        message.send()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anhang konnte nicht versandt werden: %s", exc)
        log(AuditLog.Action.NOTIFICATION_FAILED, note=f"{subject}: {exc}")
        return False, f"Der Mailversand ist fehlgeschlagen: {exc}"
    return True, ""
