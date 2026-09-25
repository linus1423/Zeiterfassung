"""Aufbewahrung und Anonymisierung (Issue 6).

Arbeitszeitnachweise müssen aufbewahrt werden, personenbezogene Daten aber
nicht unbegrenzt. Nach Ablauf der Frist (`DATA_RETENTION_MONTHS`, Vorgabe
zwei Jahre) wird das Konto anonymisiert: Name, Adresse und Personalnummer
verschwinden, die Zeiteinträge bleiben für die Statistik erhalten, sind
aber keiner Person mehr zuzuordnen.

Ein Konto wird nur angefasst, wenn seit der Frist nichts mehr passiert ist:
keine Zeiten, keine Anmeldung, kein offener Korrekturantrag. Frische Konten,
die noch nie gestempelt haben, bleiben also unberührt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.models import AuditLog, log
from apps.groups.periods import shift_months
from apps.tracking.utils import day_bounds

ANONYMOUS_LAST_NAME = "Anonymisiert"
ANONYMOUS_EMAIL_DOMAIN = "anonym.invalid"


@dataclass(frozen=True)
class Candidate:
    user: object
    last_entry: date | None

    @property
    def label(self) -> str:
        return f"{self.user.pk}"


def retention_cutoff(months: int | None = None, today: date | None = None) -> date:
    """Der Tag, vor dem Daten als abgelaufen gelten."""
    months = months if months is not None else settings.DATA_RETENTION_MONTHS
    today = today or timezone.localdate()
    return shift_months(today, -abs(int(months)))


def is_anonymized(user) -> bool:
    return user.email.endswith(f"@{ANONYMOUS_EMAIL_DOMAIN}")


def candidates(months: int | None = None, today: date | None = None) -> list[Candidate]:
    """Konten, deren Aufbewahrungsfrist abgelaufen ist."""
    from apps.corrections.models import CorrectionRequest

    User = get_user_model()
    cutoff = retention_cutoff(months, today)
    cutoff_start, _ = day_bounds(cutoff)

    # Offen sind auch Anträge, die erst eine von zwei Zustimmungen haben
    # (Gruppenwechsel eines Eintrags, Issue 37).
    pending_user_ids = set(
        CorrectionRequest.objects.filter(status__in=CorrectionRequest.OPEN_STATUSES).values_list(
            "requested_by_id", flat=True
        )
    )

    found: list[Candidate] = []
    queryset = (
        User.objects.filter(is_superuser=False, is_staff=False)
        .exclude(pk__in=pending_user_ids)
        .annotate(latest_entry=Max("time_entries__start"))
        .order_by("pk")
    )
    for user in queryset:
        if is_anonymized(user):
            continue
        if user.date_joined and user.date_joined >= cutoff_start:
            continue
        if user.last_login and user.last_login >= cutoff_start:
            continue
        latest = user.latest_entry
        if latest is not None and latest >= cutoff_start:
            continue
        last_entry = timezone.localdate(latest) if latest else None
        found.append(Candidate(user=user, last_entry=last_entry))
    return found


@transaction.atomic
def anonymize(user, *, actor=None) -> None:
    """Entfernt die personenbezogenen Angaben eines Kontos."""
    user.username = f"anonym-{user.pk}"
    user.email = f"anonym-{user.pk}@{ANONYMOUS_EMAIL_DOMAIN}"
    user.first_name = ""
    user.last_name = ANONYMOUS_LAST_NAME
    user.display_name = ""
    user.personnel_number = ""
    user.is_active = False
    user.set_unusable_password()
    user.save(
        update_fields=[
            "username",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "personnel_number",
            "is_active",
            "password",
        ]
    )

    # Ohne die Verknüpfung zum Identity-Provider ist das Konto auch dort
    # nicht mehr zuzuordnen.
    try:
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.filter(user=user).delete()
    except ImportError:  # pragma: no cover
        pass

    # Die Mitgliedschaften werden nicht mehr gebraucht, die Zeiteinträge
    # tragen ihre Gruppe selbst.
    user.group_memberships.all().delete()

    log(
        AuditLog.Action.USER_ANONYMIZED,
        actor=actor,
        subject=user,
        note="Konto nach Ablauf der Aufbewahrungsfrist anonymisiert.",
    )


def anonymize_expired(months: int | None = None, today: date | None = None, *, actor=None) -> int:
    count = 0
    for candidate in candidates(months, today):
        anonymize(candidate.user, actor=actor)
        count += 1
    return count
