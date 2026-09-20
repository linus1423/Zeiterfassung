from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("anmelden/", views.login_page, name="login"),
    path("profil/", views.profile, name="profile"),
    path("nutzer/", views.user_list, name="user_list"),
    path("nutzer/<int:user_id>/", views.user_edit, name="user_edit"),
]
