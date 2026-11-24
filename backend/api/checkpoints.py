"""Sample forecast checkpoints along a routing provider's route geometry."""

from bisect import bisect_right
from math import asin, ceil, cos, radians, sin, sqrt

from .routing_models import Checkpoint, Coordinates, Route

METERS_PER_MILE = 1_609.344
INTERVAL_MILES = (10, 25, 50)
DEFAULT_INTERVAL_MILES = 50
_MAX_INTERVALS = 120
_EARTH_RADIUS_M = 6_371_000


class CheckpointLimitError(ValueError):
    """A route would require too many checkpoints at the selected interval."""


def sample_checkpoints(route: Route, interval_miles: int = DEFAULT_INTERVAL_MILES) -> tuple[Checkpoint, ...]:
    """Sample at exact provider-distance intervals and include both endpoints."""
    if interval_miles not in INTERVAL_MILES:
        raise ValueError("interval_miles must be 10, 25, or 50")
    spacing = interval_miles * METERS_PER_MILE
    if route.distance_m > (_MAX_INTERVALS * interval_miles) * METERS_PER_MILE:
        raise CheckpointLimitError(
            f"A route exceeds {_MAX_INTERVALS * interval_miles:,} miles at the {interval_miles}-mile interval. "
            "Choose a wider interval or shorter trip."
        )

    distances = [0.0]
    for start, end in zip(route.geometry, route.geometry[1:]):
        distances.append(distances[-1] + _segment_distance(start, end))

    interior = ((index * interval_miles) * METERS_PER_MILE for index in range(1, ceil(route.distance_m / spacing)))
    offsets = (0.0, *(offset for offset in interior if offset < route.distance_m), route.distance_m)
    fractions = (0.0, *(offset / route.distance_m for offset in offsets[1:-1]), 1.0)
    return tuple(
        Checkpoint(
            route_id=route.route_id,
            position=_position_at(route.geometry, distances, fraction),
            distance_from_origin_m=offset,
            elapsed_s=route.duration_s * fraction,
        )
        for offset, fraction in zip(offsets, fractions, strict=True)
    )


def _segment_distance(start: Coordinates, end: Coordinates) -> float:
    lat1, lat2 = radians(start.latitude), radians(end.latitude)
    delta_lon = radians(_longitude_delta(start, end))
    haversine = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(min(1.0, haversine)))


def _longitude_delta(start: Coordinates, end: Coordinates) -> float:
    return (end.longitude - start.longitude + 180) % 360 - 180


def _position_at(
    geometry: tuple[Coordinates, ...], distances: list[float], fraction: float
) -> Coordinates:
    if fraction == 0 or (fraction < 1 and distances[-1] == 0):
        return geometry[0]
    if fraction == 1:
        return geometry[-1]

    target = distances[-1] * fraction
    segment = bisect_right(distances, target) - 1
    start, end = geometry[segment:segment + 2]
    progress = (target - distances[segment]) / (distances[segment + 1] - distances[segment])
    latitude = start.latitude + progress * (end.latitude - start.latitude)
    longitude = start.longitude + progress * _longitude_delta(start, end)
    return Coordinates(latitude, (longitude + 180) % 360 - 180)
