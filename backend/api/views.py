"""HTTP endpoints for the routing API."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from math import isfinite

from django.conf import settings
from django.http import JsonResponse, QueryDict
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .route_recommendations import rank_routes
from .routing_models import Coordinates, Route
from .routing_provider import RoutingCertificateError, RoutingProviderError, RoutingTimeout, find_truck_routes
from .weather_provider import WeatherProviderError, WeatherProviderTimeout
from .weather_risk import CheckpointAssessment, assess_routes_weather

_WEATHER_ATTRIBUTION = {
    "name": "Open-Meteo",
    "url": "https://open-meteo.com/",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "description": "Hourly forecasts selected for checkpoint arrivals and used to estimate weather risk.",
}


class _RoutingNotConfigured(RoutingProviderError):
    pass


@require_GET
def health(_request) -> JsonResponse:
    """Report that the API process is responding."""
    return JsonResponse({"status": "ok", "service": "weather-aware-routing-api"})


@require_GET
def truck_routes(request) -> JsonResponse:
    """Return up to three truck route options for the supplied endpoints."""
    try:
        origin, destination = _route_endpoints(request.GET)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    try:
        routes = _load_routes(origin, destination)
    except RoutingProviderError as error:
        return _routing_error(error)

    return JsonResponse({"routes": [asdict(route) for route in routes]})


@require_GET
@never_cache
def route_recommendations(request) -> JsonResponse:
    """Compare route options using forecast coverage, risk, and travel time."""
    now = datetime.now(UTC)
    try:
        origin, destination = _route_endpoints(request.GET)
        cargo_weight_kg = _cargo_weight(request.GET.getlist("cargo_weight_kg"))
        departure_at = _departure(request.GET.getlist("departure_at"), now)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    try:
        routes = _load_routes(origin, destination, depart_at=departure_at if departure_at > now else None)
    except RoutingProviderError as error:
        return _routing_error(error)

    try:
        checkpoints = assess_routes_weather(routes, departure_at, cargo_weight_kg, now=now)
    except WeatherProviderTimeout:
        return JsonResponse({"error": "Weather forecast provider timed out"}, status=504)
    except WeatherProviderError:
        return JsonResponse({"error": "Weather forecast provider is unavailable"}, status=502)
    except OverflowError:
        return JsonResponse({"error": "Truck routing provider returned an invalid travel time"}, status=502)

    ranked = rank_routes(routes, checkpoints)
    options = []
    for index, item in enumerate(ranked, start=1):
        options.append({
            **asdict(item.route),
            "rank": index if item.weather_coverage == "complete" else None,
            "weather_coverage": item.weather_coverage,
            "checkpoint_count": len(item.checkpoints),
            "scored_checkpoints": item.scored_checkpoints,
            "peak_risk_score": item.peak_risk_score,
            "mean_risk_score": round(item.mean_risk_score, 1) if item.mean_risk_score is not None else None,
            "checkpoints": [_checkpoint_data(point) for point in item.checkpoints],
        })

    recommended_id = options[0]["route_id"] if options and options[0]["rank"] == 1 else None
    return JsonResponse({
        "departure_at": departure_at.isoformat(),
        "cargo_weight_kg": cargo_weight_kg,
        "recommended_route_id": recommended_id,
        "recommendation_status": _recommendation_status(bool(options), recommended_id),
        "weather_attribution": _WEATHER_ATTRIBUTION,
        "routes": options,
    })


def _checkpoint_data(assessment: CheckpointAssessment) -> dict:
    forecast = None
    if assessment.forecast is not None:
        forecast = asdict(assessment.forecast)
        del forecast["checkpoint"]
        forecast["valid_at"] = assessment.forecast.valid_at.isoformat()
    risk = None
    if assessment.risk is not None:
        risk = {"score": assessment.risk.score, "factors": assessment.risk.factors}
    return {
        **asdict(assessment.checkpoint),
        "arrival_at": assessment.arrival_at.isoformat(),
        "forecast": forecast,
        "risk": risk,
    }


def _recommendation_status(has_routes: bool, recommended_id: str | None) -> str:
    if recommended_id is not None:
        return "recommended"
    return "insufficient_weather" if has_routes else "no_routes"


def _load_routes(origin: Coordinates, destination: Coordinates, *, depart_at: datetime | None = None) -> list[Route]:
    if not settings.TOMTOM_API_KEY:
        raise _RoutingNotConfigured
    return find_truck_routes(origin, destination, settings.TOMTOM_API_KEY, depart_at=depart_at)


def _routing_error(error: RoutingProviderError) -> JsonResponse:
    if isinstance(error, _RoutingNotConfigured):
        return JsonResponse({"error": "Truck routing is not configured"}, status=503)
    if isinstance(error, RoutingTimeout):
        return JsonResponse({"error": "Truck routing provider timed out"}, status=504)
    if isinstance(error, RoutingCertificateError):
        return JsonResponse({"error": "Could not verify the truck routing provider's TLS certificate"}, status=502)
    return JsonResponse({"error": "Truck routing provider is unavailable"}, status=502)


def _route_endpoints(query: QueryDict) -> tuple[Coordinates, Coordinates]:
    origin = _coordinates(query.getlist("origin"), "origin")
    destination = _coordinates(query.getlist("destination"), "destination")
    if origin == destination:
        raise ValueError("origin and destination must be different")
    return origin, destination


def _cargo_weight(values: list[str]) -> float:
    if len(values) != 1:
        raise ValueError("Provide exactly one cargo_weight_kg")
    try:
        weight = float(values[0])
    except ValueError:
        raise ValueError("cargo_weight_kg must be finite and nonnegative") from None
    if not isfinite(weight) or weight < 0:
        raise ValueError("cargo_weight_kg must be finite and nonnegative")
    return weight


def _departure(values: list[str], now: datetime) -> datetime:
    if not values:
        return now
    if len(values) != 1:
        raise ValueError("Provide at most one departure_at")
    try:
        departure = datetime.fromisoformat(values[0])
        if departure.utcoffset() is None:
            raise ValueError
        departure = departure.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError("departure_at must be an ISO 8601 date and time with a time zone") from None
    if not now - timedelta(minutes=1) <= departure <= now + timedelta(days=7):
        raise ValueError("departure_at must be within the next seven days")
    return departure


def _coordinates(values: list[str], field: str) -> Coordinates:
    if len(values) != 1:
        raise ValueError(f"Provide exactly one {field} as latitude,longitude")
    try:
        latitude, longitude = values[0].split(",")
        return Coordinates(float(latitude), float(longitude))
    except ValueError:
        raise ValueError(f"Invalid {field}. Use latitude,longitude in decimal degrees") from None
