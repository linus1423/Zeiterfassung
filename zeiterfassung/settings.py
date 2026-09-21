"""Django settings for the Zeiterfassung project.

All deployment specific values come from environment variables so that no
credentials ever end up in the repository. See .env.example for the full list.
"""

import os
from collections import ChainMap
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Geheimnisse, die statt als Umgebungsvariable auch als Datei übergeben werden
# dürfen: zu FOO gehört dann FOO_FILE mit dem Pfad zur Datei.
SECRETS_FROM_FILE = (
    "DJANGO_SECRET_KEY",
    "DATABASE_URL",
    "DJANGO_EMAIL_HOST_PASSWORD",
    "KEYCLOAK_CLIENT_SECRET",
    "ENTRA_CLIENT_SECRET",
)


def _secrets_from_files() -> dict[str, str]:
    """Liest Geheimnisse aus Dateien, wenn FOO_FILE statt FOO gesetzt ist.

    Podman und Docker können ein Geheimnis als Datei in den Container hängen
    (`podman secret`, `docker secret`). Das ist besser als eine
    Umgebungsvariable: die steht in `podman inspect`, im Journal eines
    fehlgeschlagenen Starts und in der Umgebung jedes Prozesses im Container.

    Die Werte werden absichtlich zurückgegeben und nicht nach `os.environ`
    geschrieben: dort stünden sie in `/proc/<pid>/environ` und würden an jeden
    Kindprozess vererbt, womit der Vorteil der Datei wieder weg wäre.

    Aufgerufen wird die Funktion, bevor die `.env` gelesen wird. Ein Name, der
    zu dem Zeitpunkt schon in der Umgebung steht, kommt deshalb gar nicht erst
    vor: eine echte Umgebungsvariable gewinnt, damit bestehende Installationen
    unverändert weiterlaufen.
    """
    secrets: dict[str, str] = {}
    for name in SECRETS_FROM_FILE:
        path = os.environ.get(f"{name}_FILE", "").strip()
        if not path or os.environ.get(name):
            continue
        try:
            # Ein abschließender Zeilenumbruch ist in solchen Dateien üblich
            # und gehört nicht zum Wert.
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ImproperlyConfigured(f"{name}_FILE ist nicht lesbar: {exc}") from exc
        if not value:
            raise ImproperlyConfigured(f"{name}_FILE ist leer: {path}")
        secrets[name] = value
    return secrets


env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_SECURE_SSL_REDIRECT=(bool, True),
    DJANGO_BEHIND_PROXY=(bool, False),
    MAX_OPEN_ENTRY_HOURS=(int, 16),
    STATUTORY_BREAK_WARNINGS=(bool, True),
    STATUTORY_LIMIT_WARNINGS=(bool, True),
    DATA_RETENTION_MONTHS=(int, 24),
    CORRECTION_EMAILS_ENABLED=(bool, False),
    REMINDER_EMAILS_ENABLED=(bool, False),
    OPEN_ENTRY_REMINDER_HOURS=(int, 10),
    PENDING_CORRECTION_REMINDER_DAYS=(int, 3),
    PENDING_CORRECTION_ESCALATION_DAYS=(int, 14),
    PERIOD_CLOSING_REMINDER_DAYS=(int, 3),
    DJANGO_EMAIL_PORT=(int, 587),
    DJANGO_EMAIL_USE_TLS=(bool, True),
    DJANGO_EMAIL_TIMEOUT=(int, 10),
    OIDC_GROUP_SYNC=(bool, False),
    OIDC_GROUPS_CLAIM=(str, "groups"),
    OIDC_GROUP_SYNC_MODE=(str, "add"),
    OIDC_ADMIN_GROUPS_CLAIM=(str, ""),
    OIDC_ADMIN_GROUP_SUFFIX=(str, ""),
    EMERGENCY_LOGIN_ENABLED=(bool, False),
    EMERGENCY_LOGIN_MAX_ATTEMPTS=(int, 5),
    EMERGENCY_LOGIN_LOCKOUT_MINUTES=(int, 15),
)
# Alles, was django-environ liest, kommt aus dieser Kette: erst die Dateien,
# dann die Prozessumgebung (in die gleich noch die .env einsortiert wird).
# Die Reihenfolge stimmt trotzdem, weil _secrets_from_files() Namen auslässt,
# die schon als Umgebungsvariable gesetzt sind.
env.ENVIRON = ChainMap(_secrets_from_files(), os.environ)
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DJANGO_DEBUG")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "django-insecure-development-key-do-not-use-in-production"  # noqa: S105

ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS") or (["localhost", "127.0.0.1"] if DEBUG else [])
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "apps.accounts",
    "apps.audit",
    "apps.groups",
    "apps.tracking",
    "apps.corrections",
    "apps.reporting",
    "apps.reminders",
]

MIDDLEWARE = [
    # Muss vorne stehen: beantwortet /healthz und /readyz, bevor die
    # Hostprüfung oder die HTTPS-Umleitung greifen (siehe health.py).
    "zeiterfassung.health.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "zeiterfassung.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "zeiterfassung.wsgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"].setdefault("CONN_MAX_AGE", 60)

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de-de"
TIME_ZONE = env("DISPLAY_TIME_ZONE", default="Europe/Berlin")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "tracking:clock"
LOGOUT_REDIRECT_URL = "accounts:login"

# --- Sicherheit -------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Wird auch beim Notfallzugang gebraucht: nur hinter einem eigenen Proxy
# darf X-Forwarded-For überhaupt geglaubt werden.
BEHIND_PROXY = env("DJANGO_BEHIND_PROXY")
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# --- Anmeldung (OpenID Connect) --------------------------------------------
# Keycloak und Entra ID sind beide OIDC und laufen deshalb über denselben
# allauth-Provider, nur mit unterschiedlicher Konfiguration.
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_ADAPTER = "apps.accounts.adapters.NoLocalSignupAdapter"
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.OIDCSocialAccountAdapter"
SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_STORE_TOKENS = False
# Ein Mensch, der sich einmal über Keycloak und einmal über Entra anmeldet,
# soll dasselbe Konto bekommen. Beide Provider sind vertrauenswürdig und
# liefern verifizierte Adressen; bei einem Provider ohne Adressverifikation
# müsste das abgeschaltet werden.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = env.bool("OIDC_LINK_BY_EMAIL", default=True)
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = SOCIALACCOUNT_EMAIL_AUTHENTICATION


def _openid_connect_apps() -> list[dict]:
    """Build the allauth provider list from the environment.

    A provider is only offered when its client id is configured, so one
    installation can run with Keycloak only, with Entra ID only, or with both.
    """
    apps: list[dict] = []

    keycloak_client_id = env("KEYCLOAK_CLIENT_ID", default="")
    if keycloak_client_id:
        apps.append(
            {
                "provider_id": "keycloak",
                "name": env("KEYCLOAK_DISPLAY_NAME", default="Keycloak"),
                "client_id": keycloak_client_id,
                "secret": env("KEYCLOAK_CLIENT_SECRET", default=""),
                "settings": {"server_url": env("KEYCLOAK_SERVER_URL", default="")},
            }
        )

    entra_client_id = env("ENTRA_CLIENT_ID", default="")
    if entra_client_id:
        tenant = env("ENTRA_TENANT_ID", default="organizations")
        apps.append(
            {
                "provider_id": "entra",
                "name": env("ENTRA_DISPLAY_NAME", default="Microsoft Entra ID"),
                "client_id": entra_client_id,
                "secret": env("ENTRA_CLIENT_SECRET", default=""),
                "settings": {
                    "server_url": env(
                        "ENTRA_SERVER_URL",
                        default=(
                            f"https://login.microsoftonline.com/{tenant}"
                            "/v2.0/.well-known/openid-configuration"
                        ),
                    )
                },
            }
        )

    return apps


SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "APPS": _openid_connect_apps(),
        "OAUTH_PKCE_ENABLED": True,
        "SCOPE": ["openid", "profile", "email"],
    }
}

# --- Notfallzugang für System-Admins (Issue 51) ----------------------------
# Die einzige Ausnahme von "Anmeldung nur über OIDC". Vorgabe ist aus: dann
# wird in apps/accounts/urls.py nicht einmal eine URL registriert, es gibt
# also weder Formular noch Endpunkt. Einschalten nur, solange der
# Identity-Provider nicht erreichbar oder noch nicht eingerichtet ist.
EMERGENCY_LOGIN_ENABLED = env("EMERGENCY_LOGIN_ENABLED")
# Fehlversuche je Benutzername und je Absenderadresse, danach gesperrt.
EMERGENCY_LOGIN_MAX_ATTEMPTS = env("EMERGENCY_LOGIN_MAX_ATTEMPTS")
EMERGENCY_LOGIN_LOCKOUT_MINUTES = env("EMERGENCY_LOGIN_LOCKOUT_MINUTES")
if EMERGENCY_LOGIN_MAX_ATTEMPTS < 1 or EMERGENCY_LOGIN_LOCKOUT_MINUTES < 1:
    raise ImproperlyConfigured(
        "EMERGENCY_LOGIN_MAX_ATTEMPTS und EMERGENCY_LOGIN_LOCKOUT_MINUTES müssen mindestens 1 sein."
    )

# --- Gruppen aus dem Identity-Provider (Issue 4) ---------------------------
# Standardmäßig aus: die Mitgliedschaften werden im Tool gepflegt. Wer die
# Gruppen aus dem Token übernehmen will, schaltet das hier ein.
OIDC_GROUP_SYNC = env("OIDC_GROUP_SYNC")
OIDC_GROUPS_CLAIM = env("OIDC_GROUPS_CLAIM")
# "add" ergänzt nur, "replace" entzieht auch wieder. Entzogen werden immer
# nur Mitgliedschaften, die aus dem Provider stammen.
OIDC_GROUP_SYNC_MODE = env("OIDC_GROUP_SYNC_MODE")
if OIDC_GROUP_SYNC_MODE not in ("add", "replace"):
    raise ImproperlyConfigured("OIDC_GROUP_SYNC_MODE must be either 'add' or 'replace'.")
# Leer bedeutet: die Admin-Rolle wird im Tool vergeben, nicht aus dem Token.
OIDC_ADMIN_GROUPS_CLAIM = env("OIDC_ADMIN_GROUPS_CLAIM")
# Alternative zum eigenen Claim: eine Gruppe "werkstatt-admins" macht zum
# Admin der Gruppe "werkstatt".
OIDC_ADMIN_GROUP_SUFFIX = env("OIDC_ADMIN_GROUP_SUFFIX")

# --- Benachrichtigungen (Issue 3) ------------------------------------------
# Ohne Mailserver bleibt es beim Zähler in der Navigation.
CORRECTION_EMAILS_ENABLED = env("CORRECTION_EMAILS_ENABLED")
# Erinnerungen, bevor ein Fehler entsteht (Issue 34). Ohne Mailserver
# stehen sie unter "Hinweise" im Tool.
REMINDER_EMAILS_ENABLED = env("REMINDER_EMAILS_ENABLED")
SITE_BASE_URL = env("SITE_BASE_URL", default="")
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="zeiterfassung@localhost")
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.console.EmailBackend"
        if DEBUG
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)
EMAIL_HOST = env("DJANGO_EMAIL_HOST", default="localhost")
EMAIL_PORT = env("DJANGO_EMAIL_PORT")
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env("DJANGO_EMAIL_USE_TLS")
# Ein hängender Mailserver darf keinen Request blockieren.
EMAIL_TIMEOUT = env("DJANGO_EMAIL_TIMEOUT")

# --- Fachliche Einstellungen ------------------------------------------------
# Nach dieser Dauer wird ein vergessener Zeiteintrag automatisch beendet und
# als unvollständig markiert (Kapitel 4 der Spezifikation).
MAX_OPEN_ENTRY_HOURS = env("MAX_OPEN_ENTRY_HOURS")
# Hinweis auf gesetzliche Pausen, ohne automatischen Abzug (Rückfrage 7).
# Gerechnet wird über den ganzen Tag, nicht über den einzelnen Eintrag.
STATUTORY_BREAK_WARNINGS = env("STATUTORY_BREAK_WARNINGS")
# Hinweis auf Höchstarbeitszeit (zehn Stunden am Tag, § 3 ArbZG) und Ruhezeit
# (elf Stunden zwischen zwei Arbeitstagen, § 5 ArbZG), ebenfalls ohne Abzug
# (Issue 49). Die Grenzwerte selbst stehen in apps/tracking/arbzg.py.
STATUTORY_LIMIT_WARNINGS = env("STATUTORY_LIMIT_WARNINGS")
# Aufbewahrungsfrist für personenbezogene Zeitdaten in Monaten (Issue 6).
# Zwei Jahre entsprechen der üblichen Frist für Arbeitszeitnachweise.
DATA_RETENTION_MONTHS = env("DATA_RETENTION_MONTHS")
# Erinnerung ans Ausstempeln. Gehört deutlich unter MAX_OPEN_ENTRY_HOURS,
# sonst kommt der Hinweis erst, wenn der Eintrag schon gekappt ist.
OPEN_ENTRY_REMINDER_HOURS = env("OPEN_ENTRY_REMINDER_HOURS")
# Frist, nach der ein offener Korrekturantrag die Admins erinnert.
PENDING_CORRECTION_REMINDER_DAYS = env("PENDING_CORRECTION_REMINDER_DAYS")
# Zweite, längere Frist: danach erfahren zusätzlich die System-Admins von dem
# liegengebliebenen Antrag (Issue 58). Gehört deutlich über die erste Frist,
# sonst eskaliert ein Antrag, den die Gruppe noch gar nicht gesehen hat.
PENDING_CORRECTION_ESCALATION_DAYS = env("PENDING_CORRECTION_ESCALATION_DAYS")
# Frist nach Ende eines Zeitraums, nach der an den Abschluss erinnert wird.
PERIOD_CLOSING_REMINDER_DAYS = env("PERIOD_CLOSING_REMINDER_DAYS")

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
