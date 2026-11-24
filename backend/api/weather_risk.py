"""Assess weather at truck checkpoints and across the hourly route corridor."""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite

from django.conf import settings

from .checkpoints import DEFAULT_INTERVAL_MILES, CheckpointLimitError, sample_checkpoints
from .routing_models import Checkpoint, CheckpointRisk, RiskCategory, Route, WeatherForecast
from .weather_provider import fetch_checkpoint_forecasts, forecast_hour

_METERS_PER_SECOND_PER_MPH = 0.44704
_KG_PER_POUND = 0.45359237
_WIND_THRESHOLDS = tuple(mph * _METERS_PER_SECOND_PER_MPH for mph in (25, 35, 45, 55))
_CORRIDOR_HOURS = 49


@dataclass(frozen=True, slots=True)
class CheckpointAssessment:
    checkpoint: Checkpoint
    arrival_at: datetime
    forecast: WeatherForecast | None
    risk: CheckpointRisk | None


@dataclass(frozen=True, slots=True)
class CorridorHour:
    hour_offset: int
    valid_at: datetime
    checkpoint_risk_categories: tuple[RiskCategory | None, ...]


@dataclass(frozen=True, slots=True)
class RouteWeather:
    checkpoints: tuple[CheckpointAssessment, ...]
    corridor_forecast: tuple[CorridorHour, ...]
    assessment_error: str | None = None


def assess_route_weather(
    route: Route,
    departure_at: datetime,
    cargo_weight_kg: float,
    *,
    now: datetime | None = None,
    interval_miles: int = DEFAULT_INTERVAL_MILES,
) -> tuple[CheckpointAssessment, ...]:
    """Sample and assess one route, preserving checkpoints without usable data."""
    assessment, = _assess((route,), departure_at, cargo_weight_kg, now, interval_miles, corridor=False)
    return assessment.checkpoints


def assess_route_corridors(
    routes: Sequence[Route],
    departure_at: datetime,
    cargo_weight_kg: float,
    *,
    now: datetime,
    interval_miles: int = DEFAULT_INTERVAL_MILES,
) -> tuple[RouteWeather, ...]:
    """Reuse the location forecasts for ETA risk and the common hourly corridor."""
    return _assess(routes, departure_at, cargo_weight_kg, now, interval_miles, corridor=True)


def _assess(
    routes: Sequence[Route], departure_at: datetime, cargo_weight_kg: float,
    now: datetime | None, interval_miles: int, *, corridor: bool,
) -> tuple[RouteWeather, ...]:
    if departure_at.utcoffset() is None:
        raise ValueError("departure_at must include a time zone")
    if not isfinite(cargo_weight_kg) or cargo_weight_kg < 0:
        raise ValueError("cargo_weight_kg must be finite and nonnegative")
    now = datetime.now(UTC) if now is None else now
    if now.utcoffset() is None:
        raise ValueError("now must include a time zone")

    departure_at = departure_at.astimezone(UTC)
    anchor = forecast_hour(now.astimezone(UTC))
    hours = tuple(anchor + timedelta(hours=index) for index in range(_CORRIDOR_HOURS)) if corridor else ()
    by_route = []
    for route in routes:
        try:
            points = sample_checkpoints(route, interval_miles)
        except CheckpointLimitError as error:
            if not corridor:
                raise
            by_route.append(([], str(error)))
            continue
        by_route.append(([(point, departure_at + timedelta(seconds=point.elapsed_s)) for point in points], None))

    if by_route and all(error is not None for _, error in by_route):
        raise CheckpointLimitError(by_route[0][1])

    requests = []
    for arrivals, _ in by_route:
        requests.extend(arrivals)
        requests.extend((point, hour) for hour in hours for point, _ in arrivals)
    forecasts = iter(fetch_checkpoint_forecasts(requests, now=now, api_key=settings.OPEN_METEO_API_KEY))

    result = []
    for arrivals, error in by_route:
        if error is not None:
            result.append(RouteWeather((), (), error))
            continue
        checkpoints = []
        for point, arrival in arrivals:
            forecast = next(forecasts)
            checkpoints.append(CheckpointAssessment(point, arrival, forecast, score_checkpoint(forecast, cargo_weight_kg)))
        corridor_forecast = tuple(
            CorridorHour(offset, hour, tuple(_risk_category(next(forecasts), cargo_weight_kg) for _ in arrivals))
            for offset, hour in enumerate(hours)
        )
        result.append(RouteWeather(tuple(checkpoints), corridor_forecast))
    return tuple(result)


def score_checkpoint(forecast: WeatherForecast | None, cargo_weight_kg: float) -> CheckpointRisk | None:
    category, factors = _classify(forecast, cargo_weight_kg)
    if forecast is None or category is None:
        return None
    return CheckpointRisk(forecast, cargo_weight_kg, category * 25, factors, category)


def _risk_category(forecast: WeatherForecast | None, cargo_weight_kg: float) -> RiskCategory | None:
    return _classify(forecast, cargo_weight_kg)[0]


def _classify(
    forecast: WeatherForecast | None, cargo_weight_kg: float,
) -> tuple[RiskCategory | None, tuple[str, ...]]:
    if forecast is None:
        return None, ()
    winds = [speed for speed in (forecast.wind_speed_m_per_s, forecast.wind_gust_m_per_s) if speed is not None]
    wind = RiskCategory(bisect_right(_WIND_THRESHOLDS, max(winds))) if winds else None
    override = None
    if wind == RiskCategory.SEVERE and cargo_weight_kg > 30_000 * _KG_PER_POUND:
        override = RiskCategory.NO_TRAVEL
    elif wind == RiskCategory.HIGH and cargo_weight_kg > 40_000 * _KG_PER_POUND:
        override = RiskCategory.SEVERE

    components = (
        ("wind", wind),
        ("rain", _precipitation_category(forecast.rain_in_per_h, (0.10, 0.25, 0.50), 1.0)),
        ("snow", _precipitation_category(forecast.snowfall_in_per_h, (0.5, 1.0, 2.0), 3.0)),
        ("load_wind_override", override),
    )
    worst = max((value for _, value in components if value is not None), default=None)
    complete = len(winds) == 2 and forecast.rain_in_per_h is not None and forecast.snowfall_in_per_h is not None
    if not complete and worst != RiskCategory.NO_TRAVEL:
        return None, ()
    return worst, tuple(name for name, value in components if value is not None and value > RiskCategory.LOW)


def _precipitation_category(
    value: float | None, thresholds: tuple[float, float, float], maximum: float,
) -> RiskCategory | None:
    if value is None:
        return None
    return RiskCategory.NO_TRAVEL if value > maximum else RiskCategory(bisect_right(thresholds, value))
