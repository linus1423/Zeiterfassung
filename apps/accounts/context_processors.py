def navigation(request):
    """Stellt die Navigationsrechte bereit, damit Templates nicht selbst prüfen."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav": {}}

    from apps.corrections import services as correction_services
    from apps.corrections.models import CorrectionRequest
    from apps.reminders import services as reminder_services

    admin_group_ids = user.administrated_group_ids()
    pending = 0
    if admin_group_ids:
        # Beim Gruppenwechsel eines Eintrags zählt auch, was auf die
        # Zustimmung der eigenen Gruppe als Zielgruppe wartet (Issue 37).
        pending = CorrectionRequest.objects.filter(
            correction_services.decidable_filter(admin_group_ids)
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
        }
    }
