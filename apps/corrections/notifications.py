"""Benachrichtigungen rund um Korrekturanträge (Issue 3).

Der Versand hängt an `transaction.on_commit`, damit keine Mail zu einem
Vorgang rausgeht, der am Ende zurückgerollt wird. Ohne konfigurierten
Mailserver bleibt es beim Zähler in der Navigation; das Tool funktioniert
also auch ohne SMTP.

Der Versand läuft im Request, begrenzt durch EMAIL_TIMEOUT. Sobald das
Mailaufkommen dafür zu groß wird, gehört hier eine Warteschlange hin.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.urls import reverse

from zeiterfassung.mailing import absolute_url, send_plain_mail

from .models import CorrectionRequest

__all__ = [
    "absolute_url",
    "emails_enabled",
    "notify_admins_of_new_request",
    "notify_requester_of_decision",
]


def emails_enabled() -> bool:
    return bool(getattr(settings, "CORRECTION_EMAILS_ENABLED", False))


def _request_context(request_obj: CorrectionRequest) -> dict:
    return {
        "correction": request_obj,
        "requester": request_obj.requested_by,
        "group": request_obj.group,
        "deciding_group": request_obj.deciding_group or request_obj.group,
        "inbox_url": absolute_url(reverse("corrections:inbox")),
        "mine_url": absolute_url(reverse("corrections:mine")),
    }


def notify_admins_of_new_request(request_obj: CorrectionRequest) -> None:
    """Meldet einen Antrag den Admins der Gruppe, die jetzt entscheidet.

    Beim Gruppenwechsel eines Eintrags (Issue 37) ist das zweimal der Fall:
    erst die bisherige Gruppe, nach deren Zustimmung die gewünschte.
    """
    if not emails_enabled():
        return

    group = request_obj.deciding_group or request_obj.group
    recipients = [
        address for address in group.admin_emails() if address != request_obj.requested_by.email
    ]
    if request_obj.status == CorrectionRequest.Status.PENDING_TARGET:
        subject = f"Gruppenwechsel zur Zustimmung: {group.name}"
    else:
        subject = f"Neuer Korrekturantrag: {group.name}"
    transaction.on_commit(
        lambda: send_plain_mail(
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
        lambda: send_plain_mail(
            subject,
            "corrections/mail/entscheidung.txt",
            _request_context(request_obj),
            [request_obj.requested_by.email],
        )
    )
