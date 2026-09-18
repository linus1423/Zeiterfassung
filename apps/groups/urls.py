from django.urls import path

from . import views

app_name = "groups"

urlpatterns = [
    path("", views.group_list, name="list"),
    path("<int:group_id>/", views.group_detail, name="detail"),
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
