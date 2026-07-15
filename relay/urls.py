from django.urls import path

from relay import views

app_name = "relay"

urlpatterns = [
    path("", views.FleetBoardView.as_view(), name="board"),
    path("drivers/", views.DriverListView.as_view(), name="drivers"),
    path("drivers/create/", views.driver_create_view, name="driver_create"),
    path("drivers/<int:pk>/", views.DriverDetailView.as_view(), name="driver_detail"),
    path("trucks/", views.TruckListView.as_view(), name="trucks"),
    path("trucks/create/", views.truck_create_view, name="truck_create"),
    path("trucks/<int:pk>/", views.TruckDetailView.as_view(), name="truck_detail"),
    path("trucks/<int:pk>/assignments/start/", views.assignment_start_view, name="assignment_start"),
    path(
        "trucks/<int:pk>/assignments/plan-next/",
        views.assignment_plan_next_view,
        name="assignment_plan_next",
    ),
    path(
        "trucks/<int:pk>/assignments/complete/",
        views.assignment_complete_view,
        name="assignment_complete",
    ),
    path(
        "trucks/<int:pk>/assignments/cancel-planned/",
        views.assignment_cancel_planned_view,
        name="assignment_cancel_planned",
    ),
]
