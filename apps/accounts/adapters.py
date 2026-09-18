from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class NoLocalSignupAdapter(DefaultAccountAdapter):
    """Verhindert lokale Registrierung.

    Konten entstehen ausschliesslich ueber den Identity-Provider. Der
    Notfallzugang fuer System-Admins laeuft ueber die Django-Adminoberflaeche.
    """

    def is_open_for_signup(self, request) -> bool:
        return False


class OIDCSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Legt Nutzer beim ersten Login aus den Angaben des Providers an."""

    def is_open_for_signup(self, request, sociallogin) -> bool:
        return True

    def populate_user(self, request, sociallogin, data):
        # Den eindeutigen Benutzernamen erzeugt allauth selbst; hier werden nur
        # die Angaben aus dem Token uebernommen.
        user = super().populate_user(request, sociallogin, data)
        email = (data.get("email") or "").strip().lower()
        if email:
            user.email = email
        name = (data.get("name") or "").strip()
        if name and not (user.first_name or user.last_name):
            user.display_name = name
        return user
