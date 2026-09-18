from allauth.socialaccount.adapter import get_adapter
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


def login_page(request):
    """Anmeldeseite mit einem Knopf je konfiguriertem OIDC-Provider."""
    if request.user.is_authenticated:
        return redirect("tracking:clock")

    providers = [
        {"name": provider.name, "url": provider.get_login_url(request, process="login")}
        for provider in get_adapter(request).list_providers(request)
    ]
    return render(request, "accounts/login.html", {"providers": providers})


@login_required
def profile(request):
    """Eigene Stammdaten und Gruppenzugehoerigkeit, nur lesend."""
    memberships = request.user.memberships().select_related("group").order_by("group__name")
    return render(request, "accounts/profile.html", {"memberships": memberships})
