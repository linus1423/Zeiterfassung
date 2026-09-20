"""Benachrichtigungen rund um Korrekturanträge (Issue 3).

Der Versand hängt an `transaction.on_commit`, damit keine Mail zu einem
Vorgang rausgeht, der am Ende zurückgerollt wird. Ohne konfigurierten
Mailserver bleibt es beim Zähler in der Navigation; das Tool funktioniert
also auch ohne SMTP.

Der Versand läuft im Request, begrenzt durch EMAIL_TIMEOUT. Sobald das
Mailaufkommen dafür zu groß wird, gehört hier eine Warteschlange hin.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from apps.audit.models import AuditLog, log

from .models import CorrectionRequest

logger = logging.getLogger(__name__)


def emails_enabled() -> bool:
    return bool(getattr(settings, "CORRECTION_EMAILS_ENABLED", False))


def absolute_url(path: str) -> str:
    """Macht aus einem Pfad einen Link, wenn die Basis-Adresse konfiguriert ist."""
    base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def _send(subject: str, template: str, context: dict, recipients: list[str]) -> None:
    recipients = sorted({address for address in recipients if address})
    if not recipients:
        return

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
        # Der Versand läuft nach dem Commit: ein Fehler hier würde sonst
        # eine Fehlerseite für einen erfolgreichen Vorgang erzeugen. Der
        # Fehlversuch steht im Protokoll.
        logger.warning("Benachrichtigung konnte nicht versandt werden: %s", exc)
        log(AuditLog.Action.NOTIFICATION_FAILED, note=f"{subject}: {exc}")


def _request_context(request_obj: CorrectionRequest) -> dict:
    return {
        "correction": request_obj,
        "requester": request_obj.requested_by,
        "group": request_obj.group,
        "inbox_url": absolute_url(reverse("corrections:inbox")),
        "mine_url": absolute_url(reverse("corrections:mine")),
    }


def notify_admins_of_new_request(request_obj: CorrectionRequest) -> None:
    """Meldet einen neuen Antrag den Admins der Gruppe."""
    if not emails_enabled():
        return

    recipients = [
        address
        for address in request_obj.group.admin_emails()
        if address != request_obj.requested_by.email
    ]
    subject = f"Neuer Korrekturantrag: {request_obj.group.name}"
    transaction.on_commit(
        lambda: _send(
            subject,
            "corrections/mail/neuer_antrag.txt",
            _request_context(request_obj),
            recipients,
        )
    )


def notify_requester_of_decision(request_obj: CorrectionRequest) -> None:
    """Meldet dem Antragsteller die Entscheidung samt Begründung."""
    if not emails_enabled():
        return

    subject = f"Korrekturantrag {request_obj.get_status_display().lower()}"
    transaction.on_commit(
        lambda: _send(
            subject,
            "corrections/mail/entscheidung.txt",
            _request_context(request_obj),
            [request_obj.requested_by.email],
        )
    )
