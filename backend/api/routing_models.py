"""In-memory data exchanged by the routing, forecast, and risk stages."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite


def _require_nonnegative(value: float, field: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{field} must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class Coordinates:
    """A geographic position in signed decimal degrees."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90 degrees")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180 degrees")


@dataclass(frozen=True, slots=True)
class Route:
    """A route alternative with geometry ordered from origin to destination."""

    route_id: str
    geometry: tuple[Coordinates, ...]
    distance_m: float
    duration_s: float

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise ValueError("route_id must not be blank")
        object.__setattr__(self, "geometry", tuple(self.geometry))
        if len(self.geometry) < 2:
            raise ValueError("route geometry must contain at least two positions")
        _require_nonnegative(self.distance_m, "distance_m")
        _require_nonnegative(self.duration_s, "duration_s")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A point along a route with distance and travel time measured from its origin."""

    route_id: str
    position: Coordinates
    distance_from_origin_m: float
    elapsed_s: float

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise ValueError("route_id must not be blank")
        _require_nonnegative(self.distance_from_origin_m, "distance_from_origin_m")
        _require_nonnegative(self.elapsed_s, "elapsed_s")


@dataclass(frozen=True, slots=True)
class WeatherForecast:
    """Weather for a checkpoint at a provider's forecast time.

    None marks a measurement the provider did not supply. A measured zero is valid.
    """

    checkpoint: Checkpoint
    valid_at: datetime
    temperature_c: float | None = None
    precipitation_mm_per_h: float | None = None
    wind_speed_m_per_s: float | None = None
    wind_gust_m_per_s: float | None = None
    visibility_m: float | None = None

    def __post_init__(self) -> None:
        if self.valid_at.utcoffset() is None:
            raise ValueError("valid_at must include a time zone")
        if self.temperature_c is not None and not isfinite(self.temperature_c):
            raise ValueError("temperature_c must be finite")

        for field in (
            "precipitation_mm_per_h",
            "wind_speed_m_per_s",
            "wind_gust_m_per_s",
            "visibility_m",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_nonnegative(value, field)


@dataclass(frozen=True, slots=True)
class CheckpointRisk:
    """A load-aware assessment of a forecast, with higher scores meaning greater risk."""

    forecast: WeatherForecast
    cargo_weight_kg: float
    score: float
    factors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonnegative(self.cargo_weight_kg, "cargo_weight_kg")
        if not 0 <= self.score <= 100:
            raise ValueError("score must be between 0 and 100")
        object.__setattr__(self, "factors", tuple(self.factors))
