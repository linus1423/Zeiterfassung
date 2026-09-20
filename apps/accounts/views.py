from allauth.socialaccount.adapter import get_adapter
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit.models import AuditLog, log

from .forms import UserSearchForm, UserStammdatenForm

User = get_user_model()

# Die Liste ist zum Nachschlagen da, nicht zum Durchblättern: wer mehr sucht, sucht.
MAX_USER_ROWS = 200


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
    """Eigene Stammdaten und Gruppenzugehörigkeit, nur lesend."""
    memberships = request.user.memberships().select_related("group").order_by("group__name")
    return render(request, "accounts/profile.html", {"memberships": memberships})


def _require_system_admin(user) -> None:
    """Nutzerstammdaten pflegt nur ein System-Admin."""
    if not user.is_superuser:
        raise PermissionDenied("Nur System-Admins dürfen Nutzer verwalten.")


@login_required
def user_list(request):
    """Alle Konten mit Personalnummer und Rolle, für System-Admins."""
    _require_system_admin(request.user)

    form = UserSearchForm(request.GET or None)
    users = User.objects.all()
    query = form.cleaned_data["q"].strip() if form.is_valid() else ""
    if query:
        users = users.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(display_name__icontains=query)
            | Q(email__icontains=query)
            | Q(personnel_number__icontains=query)
        )
    total = users.count()
    users = users.prefetch_related("group_memberships__group")[:MAX_USER_ROWS]

    return render(
        request,
        "accounts/user_list.html",
        {"form": form, "users": users, "total": total, "limit": MAX_USER_ROWS},
    )


@login_required
def user_edit(request, user_id):
    """Personalnummer und die Rolle Buchhaltung eines Kontos pflegen."""
    _require_system_admin(request.user)

    person = get_object_or_404(User, pk=user_id)
    form = UserStammdatenForm(request.POST or None, instance=person)

    if request.method == "POST" and form.is_valid():
        before = {
            "personnel_number": person.personnel_number,
            "is_accounting": person.is_accounting,
        }
        form.save()
        if form.changed_data:
            log(
                AuditLog.Action.USER_UPDATED,
                actor=request.user,
                target=person,
                subject=person,
                changes={
                    "vorher": before,
                    "nachher": {
                        "personnel_number": person.personnel_number,
                        "is_accounting": person.is_accounting,
                    },
                },
            )
        messages.success(request, f"Stammdaten von {person.full_name} gespeichert.")
        return redirect("accounts:user_list")

    memberships = person.memberships().select_related("group").order_by("group__name")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "person": person, "memberships": memberships},
    )
