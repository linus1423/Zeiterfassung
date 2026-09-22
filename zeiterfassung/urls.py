from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.tracking.urls")),
    path("gruppen/", include("apps.groups.urls")),
    path("korrekturen/", include("apps.corrections.urls")),
    path("auswertung/", include("apps.reporting.urls")),
    path("protokoll/", include("apps.audit.urls")),
    path("hinweise/", include("apps.reminders.urls")),
    path("konto/", include("apps.accounts.urls")),
    path("", include("apps.siteconfig.urls")),
    # allauth übernimmt den OIDC-Ablauf (Weiterleitung, Rückkanal, Abmelden).
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]
