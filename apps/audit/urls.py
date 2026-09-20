from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("gruppe/<int:group_id>/", views.group_log, name="group"),
    path("eintrag/<int:entry_id>/", views.entry_log, name="entry"),
]
