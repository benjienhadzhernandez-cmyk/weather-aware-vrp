"""HTTP endpoints for the routing API."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from math import isfinite

from django.conf import settings
from django.http import JsonResponse, QueryDict
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .checkpoints import DEFAULT_INTERVAL_MILES, INTERVAL_MILES, CheckpointLimitError
from .route_recommendations import RouteAssessment, rank_routes
from .routing_models import Coordinates, RiskCategory, Route
from .routing_provider import RoutingCertificateError, RoutingProviderError, RoutingTimeout, find_truck_routes
from .weather_provider import WeatherProviderError, WeatherProviderTimeout
from .weather_risk import CheckpointAssessment, CorridorHour, assess_route_corridors

_WEATHER_ATTRIBUTION = {
    "name": "Open-Meteo",
    "url": "https://open-meteo.com/",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "description": "Hourly forecasts used to estimate checkpoint arrival risk and corridor conditions.",
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
        interval_miles = _checkpoint_interval(request.GET.getlist("interval_miles"))
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    try:
        routes = _load_routes(origin, destination, depart_at=departure_at if departure_at > now else None)
    except RoutingProviderError as error:
        return _routing_error(error)

    try:
        forecasts = assess_route_corridors(routes, departure_at, cargo_weight_kg, now=now, interval_miles=interval_miles)
    except CheckpointLimitError as error:
        return JsonResponse({"error": str(error)}, status=422)
    except WeatherProviderTimeout:
        return JsonResponse({"error": "Weather forecast provider timed out"}, status=504)
    except WeatherProviderError:
        return JsonResponse({"error": "Weather forecast provider is unavailable"}, status=502)
    except OverflowError:
        return JsonResponse({"error": "Truck routing provider returned an invalid travel time"}, status=502)

    ranked = rank_routes(routes, [forecast.checkpoints for forecast in forecasts])
    forecast_by_route = dict(zip((route.route_id for route in routes), forecasts, strict=True))
    options = []
    for index, item in enumerate(ranked, start=1):
        average = round(item.average_risk_score, 1) if item.average_risk_score is not None else None
        weather = forecast_by_route[item.route.route_id]
        options.append({
            **asdict(item.route),
            "rank": index if item.eligible else None,
            "weather_coverage": item.weather_coverage,
            "checkpoint_count": len(item.checkpoints),
            "scored_checkpoints": item.scored_checkpoints,
            "risk_category": _category_name(item.risk_category),
            "severe_miles": item.severe_miles,
            "high_miles": item.high_miles,
            "no_travel_miles": item.no_travel_miles,
            "average_risk_score": average,
            "assessment_error": weather.assessment_error,
            "peak_risk_score": item.risk_category * 25 if item.risk_category is not None else None,
            "mean_risk_score": average,
            "checkpoints": [_checkpoint_data(point) for point in item.checkpoints],
            "corridor_forecast": [_corridor_hour(hour) for hour in weather.corridor_forecast],
        })

    recommended_id = options[0]["route_id"] if options and options[0]["rank"] == 1 else None
    return JsonResponse({
        "departure_at": departure_at.isoformat(),
        "cargo_weight_kg": cargo_weight_kg,
        "interval_miles": interval_miles,
        "recommended_route_id": recommended_id,
        "recommendation_status": _recommendation_status(ranked, recommended_id),
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
        risk = {
            "score": assessment.risk.score,
            "category": _category_name(assessment.risk.category),
            "factors": assessment.risk.factors,
        }
    return {
        **asdict(assessment.checkpoint),
        "arrival_at": assessment.arrival_at.isoformat(),
        "forecast": forecast,
        "risk": risk,
    }


def _category_name(category: RiskCategory | None) -> str | None:
    return category.name.lower() if category is not None else None


def _corridor_hour(hour: CorridorHour) -> dict:
    categories = hour.checkpoint_risk_categories
    known = [category for category in categories if category is not None]
    peak = max(known, default=None)
    if len(known) != len(categories) and peak != RiskCategory.NO_TRAVEL:
        peak = None
    return {
        "hour_offset": hour.hour_offset,
        "valid_at": hour.valid_at.isoformat(),
        "risk_category": _category_name(peak),
        "scored_checkpoints": len(known),
        "checkpoint_count": len(categories),
        "checkpoint_risk_categories": [_category_name(category) for category in categories],
    }


def _recommendation_status(routes: list[RouteAssessment], recommended_id: str | None) -> str:
    if recommended_id is not None:
        return "recommended"
    if not routes:
        return "no_routes"
    if all(route.risk_category == RiskCategory.NO_TRAVEL for route in routes):
        return "no_travel"
    return "insufficient_weather"


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


def _checkpoint_interval(values: list[str]) -> int:
    if not values:
        return DEFAULT_INTERVAL_MILES
    if len(values) != 1 or values[0] not in tuple(map(str, INTERVAL_MILES)):
        raise ValueError("interval_miles must be 10, 25, or 50")
    return int(values[0])


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
