from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("anmelden/", views.login_page, name="login"),
    path("profil/", views.profile, name="profile"),
]
