from django.urls import path

from . import views

app_name = "corrections"

urlpatterns = [
    path("", views.my_requests, name="mine"),
    path("neu/", views.request_create, name="create"),
    path("neu/<int:entry_id>/", views.request_create, name="create_for_entry"),
    path("loeschung/<int:entry_id>/", views.request_delete, name="request_delete"),
    path("gruppenwechsel/<int:entry_id>/", views.request_move, name="request_move"),
    path("<int:request_id>/zuruecknehmen/", views.request_withdraw, name="withdraw"),
    path("eingang/", views.inbox, name="inbox"),
    path("eingang/<int:request_id>/", views.decide, name="decide"),
]
