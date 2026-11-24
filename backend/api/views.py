"""HTTP endpoints for the routing API."""

from dataclasses import asdict

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .routing_models import Coordinates
from .routing_provider import RoutingProviderError, RoutingTimeout, find_truck_routes


@require_GET
def health(_request) -> JsonResponse:
    """Report that the API process is responding."""
    return JsonResponse({"status": "ok", "service": "weather-aware-routing-api"})


@require_GET
def truck_routes(request) -> JsonResponse:
    """Return up to three truck route options for the supplied endpoints."""
    try:
        origin = _coordinates(request.GET.getlist("origin"), "origin")
        destination = _coordinates(request.GET.getlist("destination"), "destination")
        if origin == destination:
            raise ValueError("origin and destination must be different")
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    if not settings.TOMTOM_API_KEY:
        return JsonResponse({"error": "Truck routing is not configured"}, status=503)

    try:
        routes = find_truck_routes(origin, destination, settings.TOMTOM_API_KEY)
    except RoutingTimeout:
        return JsonResponse({"error": "Truck routing provider timed out"}, status=504)
    except RoutingProviderError:
        return JsonResponse({"error": "Truck routing provider is unavailable"}, status=502)

    return JsonResponse({"routes": [asdict(route) for route in routes]})


def _coordinates(values: list[str], field: str) -> Coordinates:
    if len(values) != 1:
        raise ValueError(f"Provide exactly one {field} as latitude,longitude")
    try:
        latitude, longitude = values[0].split(",")
        return Coordinates(float(latitude), float(longitude))
    except ValueError:
        raise ValueError(f"Invalid {field}. Use latitude,longitude in decimal degrees") from None
