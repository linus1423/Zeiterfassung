from django.conf import settings
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("anmelden/", views.login_page, name="login"),
    path("profil/", views.profile, name="profile"),
    path("nutzer/", views.user_list, name="user_list"),
    path("nutzer/<int:user_id>/", views.user_edit, name="user_edit"),
]

# Der Notfallzugang existiert nur, wenn er ausdrücklich eingeschaltet ist
# (Issue 51). Ausgeschaltet gibt es das Muster nicht: keine URL, kein
# Formular, und {% url 'accounts:emergency_login' %} schlägt fehl, statt
# still etwas anzubieten, was es nicht geben soll.
if settings.EMERGENCY_LOGIN_ENABLED:
    urlpatterns.append(path("notfall-anmeldung/", views.emergency_login, name="emergency_login"))
