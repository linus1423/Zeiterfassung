from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.export_view, name="export"),
    path("vorlage/<int:profile_id>/", views.export_view, name="export_profile"),
    path("vorlage/<int:profile_id>/loeschen/", views.profile_delete, name="profile_delete"),
]
