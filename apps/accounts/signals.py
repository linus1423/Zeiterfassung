"""Was beim Login passiert: Gruppen aus dem Token uebernehmen (Issue 4)."""

import logging

from allauth.account.signals import user_logged_in
from django.dispatch import receiver

from .idp_groups import claims_from_sociallogin, sync_memberships

logger = logging.getLogger(__name__)


@receiver(user_logged_in)
def sync_groups_on_login(sender, request, user, sociallogin=None, **kwargs):
    """Uebernimmt die Gruppen des Identity-Providers, wenn das eingeschaltet ist.

    Ein Fehler hier darf die Anmeldung nicht verhindern: wer sich nicht
    anmelden kann, kann auch nicht stempeln.
    """
    if sociallogin is None:
        return
    try:
        sync_memberships(user, claims_from_sociallogin(sociallogin))
    except Exception:  # noqa: BLE001
        logger.exception("Gruppenabgleich beim Login fehlgeschlagen")
