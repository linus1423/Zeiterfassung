import hashlib

from allauth.socialaccount.adapter import get_adapter
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.audit.models import AuditLog, log

from .forms import EmergencyLoginForm, UserSearchForm, UserStammdatenForm

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
    return render(
        request,
        "accounts/login.html",
        {
            "providers": providers,
            # Nur ein Hinweis für die Anzeige: ob der Weg wirklich offen ist,
            # entscheidet die URL, die es ohne den Schalter gar nicht gibt.
            "notfallzugang": settings.EMERGENCY_LOGIN_ENABLED,
        },
    )


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


# --- Notfallzugang (Issue 51) ----------------------------------------------
# Dieselbe Meldung für jeden Fehlschlag. Wer hier probiert, soll nicht erfahren,
# ob es den Benutzernamen gibt, ob das Passwort stimmte oder ob das Konto bloß
# kein System-Admin ist.
EMERGENCY_ERROR = (
    "Anmeldung nicht möglich. Benutzername oder Passwort stimmt nicht, "
    "oder das Konto ist kein System-Admin."
)
EMERGENCY_BLOCKED = (
    "Zu viele Fehlversuche. Der Notfallzugang ist vorübergehend gesperrt, "
    "bitte später erneut versuchen."
)


def _emergency_client_address(request) -> str:
    """Die Absenderadresse, gegen die gezählt wird.

    X-Forwarded-For wird nur ausgewertet, wenn die Installation laut
    DJANGO_BEHIND_PROXY hinter einem eigenen Reverse Proxy steht, und dann nur
    der letzte Eintrag: den hat der eigene Proxy angehängt, alles davor darf
    der Client frei erfinden. Ohne Proxy zählt allein REMOTE_ADDR, das sich
    nicht fälschen lässt.
    """
    if settings.BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.rsplit(",", 1)[-1].strip()[:64]
    return request.META.get("REMOTE_ADDR") or "unbekannt"


def _emergency_keys(request, username: str) -> list[str]:
    """Die Zähler dieses Versuchs: einer je Benutzername, einer je Absender.

    Beides wird gehasht, damit der Cache-Schlüssel garantiert aus erlaubten
    Zeichen besteht und im Cache kein Benutzername im Klartext liegt. Der Hash
    ist hier nur ein Schlüssel und schützt kein Geheimnis.
    """
    parts = [f"name:{username.strip().casefold()}", f"ip:{_emergency_client_address(request)}"]
    return [
        f"notfall-login:{hashlib.sha256(part.encode('utf-8')).hexdigest()[:32]}" for part in parts
    ]


def _emergency_blocked(keys: list[str]) -> bool:
    return any((cache.get(key) or 0) >= settings.EMERGENCY_LOGIN_MAX_ATTEMPTS for key in keys)


def _emergency_count_failure(keys: list[str]) -> int:
    """Zählt den Fehlversuch hoch und gibt den höchsten Stand zurück.

    Jeder Fehlversuch setzt die Frist neu, solange noch nicht gesperrt ist.
    Sobald die Sperre steht, wird gar nicht mehr gezählt: sie läuft dann in
    Ruhe ab, statt sich durch weiteres Klopfen endlos zu verlängern — sonst
    könnte man einem System-Admin den Notfallzugang dauerhaft zuhalten.

    Bewusst über den Cache und nicht über die Datenbank: eine Anmeldeseite,
    die es für den Notfall gibt, soll von so wenig wie möglich abhängen.
    """
    window = settings.EMERGENCY_LOGIN_LOCKOUT_MINUTES * 60
    hoechster = 0
    for key in keys:
        count = (cache.get(key) or 0) + 1
        cache.set(key, count, timeout=window)
        hoechster = max(hoechster, count)
    return hoechster


def _emergency_reset(keys: list[str]) -> None:
    for key in keys:
        cache.delete(key)


@never_cache
@require_http_methods(["GET", "POST"])
def emergency_login(request):
    """Lokale Anmeldung mit Passwort, ausschließlich für System-Admins.

    Fällt der Identity-Provider aus oder ist er falsch konfiguriert, kommt
    sonst niemand mehr in die Anwendung, auch kein System-Admin, und man kann
    es auch nicht von innen reparieren (Rückfrage 12 der Spezifikation).

    Das ist die einzige Ausnahme von "Anmeldung nur über OIDC". Warum sie
    trotzdem dicht ist:

    * **Standardmäßig gibt es sie nicht.** Ohne EMERGENCY_LOGIN_ENABLED wird
      das URL-Muster gar nicht erst registriert (apps/accounts/urls.py): keine
      URL, kein Formular, kein Endpunkt, der sich abklopfen ließe. Die Prüfung
      am Anfang dieser Funktion ist nur die zweite Reihe für den Fall, dass die
      View anderswo eingehängt wird.
    * **Nur ModelBackend, und das ist ohnehin schon aktiv.** Geprüft wird
      direkt gegen ModelBackend statt über authenticate(), damit hier nie ein
      anderes Backend einspringt. Es kommt keine Anmeldemöglichkeit hinzu, die
      es nicht vorher schon gab: das ModelBackend steht seit jeher in
      AUTHENTICATION_BACKENDS, weil die Django-Adminoberfläche unter /admin/
      es braucht. Der Notfallzugang macht also keinen neuen Weg auf, er macht
      den vorhandenen sichtbar, protokolliert und begrenzt.
    * **is_superuser wird nach der Passwortprüfung erzwungen.** Ein Konto ohne
      is_superuser kommt hier auch mit dem richtigen Passwort nicht hinein; die
      Sitzung entsteht erst danach, vorher passiert nichts Anmeldendes.
      Inaktive Konten filtert ModelBackend selbst heraus.
    * **Kein Nutzen ohne gesetztes Passwort.** Über den Identity-Provider
      angelegte Konten haben ein unbrauchbares Passwort
      (set_unusable_password), gegen das keine Eingabe passt. Ein Passwort muss
      ein Mensch bewusst setzen, siehe README.
    * **Keine Auskunft über Konten.** Jeder Fehlschlag liefert dieselbe
      Meldung, und ModelBackend hasht auch bei unbekanntem Benutzernamen, damit
      sich die Konten nicht an der Antwortzeit unterscheiden lassen.
    * **Kein offenes Weiterleitungsziel.** Das Ziel nach der Anmeldung steht
      fest, ein ?next= aus dem Request wird nicht angesehen.
    * **Protokoll ohne Geheimnisse.** Gespeichert werden Benutzername, Grund
      und Absenderadresse, niemals das Passwort; das Formularfeld wird auch
      nicht zurückgespielt (render_value=False). Geschrieben wird vor der
      Anmeldung, damit es keine Sitzung ohne Protokolleintrag geben kann.
      Bleibt ein Rest: wer sein Passwort aus Versehen ins Feld Benutzername
      tippt, dessen Eingabe steht im Protokoll. Das ist der Preis dafür, den
      versuchten Benutzernamen überhaupt festzuhalten, und das Protokoll
      sehen ohnehin nur Admins.
    * **Begrenzte Versuche.** Nach EMERGENCY_LOGIN_MAX_ATTEMPTS Fehlversuchen
      je Benutzername und je Absenderadresse ist für
      EMERGENCY_LOGIN_LOCKOUT_MINUTES Minuten Schluss. Während der Sperre wird
      gar nicht erst gehasht und kein weiterer Protokolleintrag geschrieben,
      sonst wäre das Durchprobieren ein billiger Weg, die Datenbank zu fluten.

    Bekannte Grenze: der Zähler liegt im Django-Cache. In der Vorgabe ist das
    ein Cache je Prozess, die Grenze gilt dann je Arbeitsprozess. Wer das
    genauer braucht, richtet einen gemeinsamen Cache ein (README).
    """
    if not settings.EMERGENCY_LOGIN_ENABLED:
        raise Http404

    if request.user.is_authenticated:
        return redirect("tracking:clock")

    form = EmergencyLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        absender = _emergency_client_address(request)
        keys = _emergency_keys(request, username)

        if _emergency_blocked(keys):
            form.add_error(None, EMERGENCY_BLOCKED)
            return render(request, "accounts/emergency_login.html", {"form": form})

        # Erst hier wird gehasht. Gegen ein unbekanntes Konto prüft
        # ModelBackend gegen einen Platzhalter, damit die Antwortzeit gleich
        # bleibt; is_active prüft es dabei selbst.
        person = ModelBackend().authenticate(
            request, username=username, password=form.cleaned_data["password"]
        )

        if person is not None and person.is_superuser:
            _emergency_reset(keys)
            # Erst protokollieren, dann anmelden: scheitert das Schreiben,
            # entsteht auch keine Sitzung. Ein Notfallzugang, der still
            # benutzt werden kann, wäre das Gegenteil von dem, was er soll.
            log(
                AuditLog.Action.EMERGENCY_LOGIN,
                actor=person,
                subject=person,
                changes={"benutzername": username, "absender": absender},
                note="Notfallzugang genutzt.",
            )
            login(request, person, backend="django.contrib.auth.backends.ModelBackend")
            messages.warning(
                request,
                "Über den Notfallzugang angemeldet. Schalte ihn wieder aus, "
                "sobald die Anmeldung über den Identity-Provider läuft.",
            )
            return redirect("admin:index" if person.is_staff else "tracking:clock")

        grund = "Kein System-Admin." if person is not None else "Benutzername oder Passwort falsch."
        versuche = _emergency_count_failure(keys)
        gesperrt = versuche >= settings.EMERGENCY_LOGIN_MAX_ATTEMPTS
        log(
            AuditLog.Action.EMERGENCY_LOGIN_FAILED,
            # Kein actor: es hat sich niemand angemeldet. Wenn das Passwort
            # stimmte, aber die Rolle fehlte, steht das Konto als betroffene
            # Person darin.
            subject=person,
            changes={"benutzername": username, "absender": absender},
            note=grund + (" Zugang jetzt gesperrt." if gesperrt else ""),
        )
        form.add_error(None, EMERGENCY_BLOCKED if gesperrt else EMERGENCY_ERROR)

    return render(request, "accounts/emergency_login.html", {"form": form})
