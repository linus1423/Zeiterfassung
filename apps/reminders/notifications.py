"""Mail zu einer Erinnerung (Issue 34).

Abschaltbar über REMINDER_EMAILS_ENABLED und unschädlich, wenn der
Mailserver nicht erreichbar ist: der Hinweis steht ohnehin im Tool.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from zeiterfassung.mailing import absolute_url, send_plain_mail

from .models import Reminder


def emails_enabled() -> bool:
    return bool(getattr(settings, "REMINDER_EMAILS_ENABLED", False))


def deliver(reminder: Reminder, subject: str, template: str, context: dict) -> bool:
    """Verschickt die Mail zu einer neu entstandenen Erinnerung."""
    if not emails_enabled() or not reminder.recipient.email:
        return False

    payload = {"reminder": reminder, "link": absolute_url(reminder.url), **context}
    if not send_plain_mail(subject, template, payload, [reminder.recipient.email]):
        return False

    reminder.emailed_at = timezone.now()
    reminder.save(update_fields=["emailed_at"])
    return True
