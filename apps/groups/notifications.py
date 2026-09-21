"""Benachrichtigungen rund um den Gruppenwechsel (Issue 37).

Wie bei den Korrekturanträgen hängt der Versand an `transaction.on_commit`,
damit keine Mail zu einem Vorgang rausgeht, der am Ende zurückgerollt wird.
Ohne konfigurierten Mailserver bleibt es bei der Anzeige im Tool.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.urls import reverse

from zeiterfassung.mailing import absolute_url, send_plain_mail

from .models import GroupChangeRequest

__all__ = ["emails_enabled", "notify_group_change_step"]


def emails_enabled() -> bool:
    return bool(getattr(settings, "GROUP_CHANGE_EMAILS_ENABLED", False))


def _recipients(change: GroupChangeRequest) -> tuple[list[str], str]:
    """Wer über diesen Stand zu unterrichten ist, und mit welchem Betreff.

    Solange der Antrag offen ist, ist die Gruppe dran, die entscheiden soll;
    ihre Admins bekommen die Mail. Ist er entschieden, erfährt es der
    Antragsteller.
    """
    if change.status == GroupChangeRequest.Status.PENDING_SOURCE:
        return change.from_group.admin_emails(), "Neuer Antrag auf Gruppenwechsel"
    if change.status == GroupChangeRequest.Status.PENDING_TARGET:
        return change.to_group.admin_emails(), "Antrag auf Gruppenwechsel in deine Gruppe"
    return [change.user.email], f"Gruppenwechsel {change.get_status_display().lower()}"


def notify_group_change_step(change: GroupChangeRequest) -> None:
    """Meldet den aktuellen Stand an die Stelle, die jetzt am Zug ist."""
    if not emails_enabled():
        return

    recipients, subject = _recipients(change)
    if not change.is_decided:
        # Über den eigenen Antrag braucht der Antragsteller keine Mail, auch
        # wenn er in der entscheidenden Gruppe selbst Admin ist.
        recipients = [address for address in recipients if address != change.user.email]
    recipients = [address for address in recipients if address]
    if not recipients:
        return

    context = {
        "change": change,
        "requester": change.user,
        "from_group": change.from_group,
        "to_group": change.to_group,
        "inbox_url": absolute_url(reverse("groups:change_inbox")),
        "mine_url": absolute_url(reverse("groups:change_list")),
    }
    transaction.on_commit(
        lambda: send_plain_mail(subject, "groups/mail/gruppenwechsel.txt", context, recipients)
    )
