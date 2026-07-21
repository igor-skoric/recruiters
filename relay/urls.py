from django.urls import path

from relay import views

app_name = "relay"

urlpatterns = [
    path("", views.FleetBoardView.as_view(), name="board"),
    path("drivers/", views.DriverListView.as_view(), name="drivers"),
    path("drivers/create/", views.driver_create_view, name="driver_create"),
    path("drivers/<int:pk>/edit/", views.driver_edit_view, name="driver_edit"),
    path("drivers/<int:pk>/delete/", views.driver_delete_view, name="driver_delete"),
    path("drivers/import/", views.drivers_import_view, name="drivers_import"),
    path("drivers/<int:pk>/", views.DriverDetailView.as_view(), name="driver_detail"),
    path(
        "drivers/<int:pk>/assignments/home-time/",
        views.driver_assignment_update_home_time_view,
        name="driver_assignment_update_home_time",
    ),
    path(
        "drivers/<int:pk>/home-time-period/",
        views.driver_home_time_period_update_view,
        name="driver_home_time_period_update",
    ),
    path("trucks/", views.TruckListView.as_view(), name="trucks"),
    path("trucks/create/", views.truck_create_view, name="truck_create"),
    path("trucks/<int:pk>/edit/", views.truck_edit_view, name="truck_edit"),
    path("trucks/<int:pk>/delete/", views.truck_delete_view, name="truck_delete"),
    path("trucks/import/", views.trucks_import_view, name="trucks_import"),
    path("trucks/<int:pk>/", views.TruckDetailView.as_view(), name="truck_detail"),
    path(
        "import-templates/<str:kind>/",
        views.import_template_view,
        name="import_template",
    ),
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
        "trucks/<int:pk>/assignments/home-time/",
        views.assignment_update_home_time_view,
        name="assignment_update_home_time",
    ),
    path(
        "trucks/<int:pk>/assignments/cancel-planned/",
        views.assignment_cancel_planned_view,
        name="assignment_cancel_planned",
    ),
]
