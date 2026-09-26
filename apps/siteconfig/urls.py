from django.urls import path

from . import views

app_name = "siteconfig"

urlpatterns = [
    path("darstellung/", views.site_settings, name="settings"),
    path("darstellung/logo", views.logo, name="logo"),
    path("mail-domaenen/", views.mail_domains, name="mail_domains"),
    path("impressum/", views.imprint, name="imprint"),
    path("datenschutz/", views.privacy, name="privacy"),
]
