def navigation(request):
    """Stellt die Navigationsrechte bereit, damit Templates nicht selbst pruefen."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav": {}}
    return {
        "nav": {
            "is_group_admin": user.is_any_group_admin,
            "is_accounting": user.is_accounting,
            "is_superuser": user.is_superuser,
        }
    }
