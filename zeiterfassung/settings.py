"""Django settings for the Zeiterfassung project.

All deployment specific values come from environment variables so that no
credentials ever end up in the repository. See .env.example for the full list.
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_SECURE_SSL_REDIRECT=(bool, True),
    DJANGO_BEHIND_PROXY=(bool, False),
    MAX_OPEN_ENTRY_HOURS=(int, 16),
    STATUTORY_BREAK_WARNINGS=(bool, True),
)
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
]

MIDDLEWARE = [
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

if env("DJANGO_BEHIND_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# --- Anmeldung (OpenID Connect) --------------------------------------------
# Keycloak und Entra ID sind beide OIDC und laufen deshalb ueber denselben
# allauth-Provider, nur mit unterschiedlicher Konfiguration.
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_ADAPTER = "apps.accounts.adapters.NoLocalSignupAdapter"
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.OIDCSocialAccountAdapter"
SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_STORE_TOKENS = False
# Ein Mensch, der sich einmal ueber Keycloak und einmal ueber Entra anmeldet,
# soll dasselbe Konto bekommen. Beide Provider sind vertrauenswuerdig und
# liefern verifizierte Adressen; bei einem Provider ohne Adressverifikation
# muesste das abgeschaltet werden.
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

# --- Fachliche Einstellungen ------------------------------------------------
# Nach dieser Dauer wird ein vergessener Zeiteintrag automatisch beendet und
# als unvollstaendig markiert (Kapitel 4 der Spezifikation).
MAX_OPEN_ENTRY_HOURS = env("MAX_OPEN_ENTRY_HOURS")
# Hinweis auf gesetzliche Pausen, ohne automatischen Abzug (Rueckfrage 7).
STATUTORY_BREAK_WARNINGS = env("STATUTORY_BREAK_WARNINGS")

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
