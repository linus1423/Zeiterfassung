from django.urls import path

from . import views

app_name = "reminders"

urlpatterns = [
    path("", views.reminder_list, name="list"),
    path("<int:reminder_id>/ausblenden/", views.reminder_dismiss, name="dismiss"),
]
