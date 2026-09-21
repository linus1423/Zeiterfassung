def navigation(request):
    """Stellt die Navigationsrechte bereit, damit Templates nicht selbst prüfen."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav": {}}

    from apps.corrections.models import CorrectionRequest
    from apps.groups import change_requests
    from apps.reminders import services as reminder_services

    admin_group_ids = user.administrated_group_ids()
    pending = 0
    if admin_group_ids:
        pending = CorrectionRequest.objects.filter(
            group_id__in=admin_group_ids, status=CorrectionRequest.Status.PENDING
        ).count()

    # Entscheidungen zu eigenen Anträgen, die der Nutzer noch nicht gesehen
    # hat. Damit erfährt er sie auch ohne Mailserver (Issue 3).
    new_decisions = CorrectionRequest.objects.filter(
        requested_by=user,
        status__in=(CorrectionRequest.Status.APPROVED, CorrectionRequest.Status.REJECTED),
        decision_seen_at__isnull=True,
    ).count()

    return {
        "nav": {
            "is_group_admin": user.is_any_group_admin,
            "is_accounting": user.is_accounting,
            "is_superuser": user.is_superuser,
            "pending_corrections": pending,
            "new_decisions": new_decisions,
            # Hinweise, die Arbeit ersparen, bevor etwas schiefgeht (Issue 34).
            "reminders": reminder_services.open_count(user),
            # Gruppenwechsel laufen über zwei Zustimmungen (Issue 37): die
            # Zähler zeigen, was gerade an einem selbst hängt.
            **change_requests.navigation_counts(user, admin_group_ids),
        }
    }
