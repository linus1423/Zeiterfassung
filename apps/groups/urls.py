from django.urls import path

from . import views

app_name = "groups"

urlpatterns = [
    path("", views.group_list, name="list"),
    path("neu/", views.group_create, name="create"),
    path("abschluesse/", views.closing_overview, name="closing_overview"),
    path("<int:group_id>/", views.group_detail, name="detail"),
    path("<int:group_id>/einstellungen/", views.group_settings, name="settings"),
    path("<int:group_id>/abschluss/", views.period_list, name="periods"),
    path("<int:group_id>/auffaelligkeiten/", views.anomalies, name="anomalies"),
    path("<int:group_id>/zeiten/neu/", views.entry_create, name="entry_create"),
    path("<int:group_id>/zeiten/<int:entry_id>/", views.entry_edit, name="entry_edit"),
    path("<int:group_id>/taetigkeiten/", views.activity_list, name="activities"),
    path(
        "<int:group_id>/taetigkeiten/<int:activity_id>/", views.activity_edit, name="activity_edit"
    ),
    path("<int:group_id>/mitglieder/", views.member_list, name="members"),
    path(
        "<int:group_id>/mitglieder/<int:membership_id>/rolle/",
        views.member_role,
        name="member_role",
    ),
    path(
        "<int:group_id>/mitglieder/<int:membership_id>/entfernen/",
        views.member_remove,
        name="member_remove",
    ),
]
