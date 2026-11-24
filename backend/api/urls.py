"""API endpoint routes."""

from django.urls import path

from .views import health, truck_routes


urlpatterns = [
    path("health/", health, name="health"),
    path("routes/", truck_routes, name="truck_routes"),
]
