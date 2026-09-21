from django.urls import path

from . import import_views, views

app_name = "tracking"

urlpatterns = [
    path("", views.clock, name="clock"),
    path("stempeln/start/", views.clock_in_view, name="clock_in"),
    path("stempeln/taetigkeit/", views.switch_activity_view, name="switch_activity"),
    path("stempeln/pause/", views.break_start_view, name="break_start"),
    path("stempeln/weiter/", views.break_end_view, name="break_end"),
    path("stempeln/stop/", views.clock_out_view, name="clock_out"),
    path("meine-zeiten/", views.my_entries, name="my_entries"),
    path("zeiten-import/", import_views.import_entries, name="import_entries"),
    path("zeiten-import/vorlage.csv", import_views.import_sample, name="import_sample"),
    path(
        "zeiten-import/<int:import_id>/uebernehmen/",
        import_views.import_apply,
        name="import_apply",
    ),
]
