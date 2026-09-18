from django.urls import path

from . import views

app_name = "tracking"

urlpatterns = [
    path("", views.clock, name="clock"),
    path("stempeln/start/", views.clock_in_view, name="clock_in"),
    path("stempeln/pause/", views.break_start_view, name="break_start"),
    path("stempeln/weiter/", views.break_end_view, name="break_end"),
    path("stempeln/stop/", views.clock_out_view, name="clock_out"),
    path("meine-zeiten/", views.my_entries, name="my_entries"),
]
