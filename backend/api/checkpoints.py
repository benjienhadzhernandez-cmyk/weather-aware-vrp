"""Sample forecast checkpoints along a routing provider's route geometry."""

from bisect import bisect_right
from math import asin, ceil, cos, radians, sin, sqrt

from .routing_models import Checkpoint, Coordinates, Route

_TARGET_SPACING_M = 50_000
_MAX_CHECKPOINTS = 12
_EARTH_RADIUS_M = 6_371_000


def sample_checkpoints(route: Route) -> tuple[Checkpoint, ...]:
    """Include both endpoints and space interior checkpoints along the polyline."""
    distances = [0.0]
    for start, end in zip(route.geometry, route.geometry[1:]):
        distances.append(distances[-1] + _segment_distance(start, end))

    segments = min(_MAX_CHECKPOINTS - 1, max(1, ceil(route.distance_m / _TARGET_SPACING_M)))
    if distances[-1] == 0:
        segments = 1
    return tuple(
        Checkpoint(
            route_id=route.route_id,
            position=_position_at(route.geometry, distances, index / segments),
            distance_from_origin_m=route.distance_m * (index / segments),
            elapsed_s=route.duration_s * (index / segments),
        )
        for index in range(segments + 1)
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
    if fraction == 0:
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
