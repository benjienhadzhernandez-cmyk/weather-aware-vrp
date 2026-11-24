"""Estimate weather exposure and cargo-aware risk at route checkpoints."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite

from django.conf import settings

from .checkpoints import sample_checkpoints
from .routing_models import Checkpoint, CheckpointRisk, Route, WeatherForecast
from .weather_provider import fetch_checkpoint_forecasts


@dataclass(frozen=True, slots=True)
class CheckpointAssessment:
    checkpoint: Checkpoint
    arrival_at: datetime
    forecast: WeatherForecast | None
    risk: CheckpointRisk | None


def assess_route_weather(
    route: Route,
    departure_at: datetime,
    cargo_weight_kg: float,
    *,
    now: datetime | None = None,
) -> tuple[CheckpointAssessment, ...]:
    """Sample and assess one route, preserving checkpoints without usable data."""
    if departure_at.utcoffset() is None:
        raise ValueError("departure_at must include a time zone")
    if not isfinite(cargo_weight_kg) or cargo_weight_kg < 0:
        raise ValueError("cargo_weight_kg must be finite and nonnegative")

    now = datetime.now(UTC) if now is None else now
    departure_at = departure_at.astimezone(UTC)
    arrivals = [
        (checkpoint, departure_at + timedelta(seconds=checkpoint.elapsed_s))
        for checkpoint in sample_checkpoints(route)
    ]
    forecasts = fetch_checkpoint_forecasts(
        arrivals,
        now=now,
        api_key=settings.OPEN_METEO_API_KEY,
    )
    return tuple(
        CheckpointAssessment(checkpoint, arrival, forecast, _score(forecast, cargo_weight_kg))
        for (checkpoint, arrival), forecast in zip(arrivals, forecasts, strict=True)
    )


def _score(forecast: WeatherForecast | None, cargo_weight_kg: float) -> CheckpointRisk | None:
    if forecast is None:
        return None
    temperature = forecast.temperature_c
    precipitation = forecast.precipitation_mm_per_h
    wind_speed = forecast.wind_speed_m_per_s
    gust = forecast.wind_gust_m_per_s
    visibility = forecast.visibility_m
    if temperature is None or precipitation is None or wind_speed is None or gust is None or visibility is None:
        return None

    # Light loads increase wind exposure. Cargo weight alone does not establish vehicle stability.
    light_load_factor = 1 + 0.35 * (1 - min(cargo_weight_kg / 20_000, 1))
    wind = 45 * _severity(max(wind_speed, gust), 10, 30) * light_load_factor
    rain = 30 * _severity(precipitation, 0, 20)
    low_visibility = 25 * _severity(2_000 - visibility, 0, 1_800)
    possible_icing = 20 if temperature <= 1 and precipitation > 0 else 0
    components = (
        ("wind", wind),
        ("precipitation", rain),
        ("low_visibility", low_visibility),
        ("possible_icing", possible_icing),
    )
    return CheckpointRisk(
        forecast=forecast,
        cargo_weight_kg=cargo_weight_kg,
        score=round(min(100, sum(points for _, points in components)), 1),
        factors=tuple(name for name, points in components if points > 0),
    )


def _severity(value: float, start: float, maximum: float) -> float:
    return min(1, max(0, (value - start) / (maximum - start)))
