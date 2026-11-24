"""TomTom adapter for truck route alternatives."""

import json
from decimal import Decimal
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .routing_models import Coordinates, Route

_BASE_URL = "https://api.tomtom.com/routing/1/calculateRoute"
_MAX_ROUTES = 3
_TIMEOUT_S = 8
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_NO_ROUTE_CODES = {"NO_ROUTE_FOUND", "MAP_MATCHING_FAILURE"}


class RoutingProviderError(Exception):
    """The provider failed or returned an unusable response."""


class RoutingTimeout(RoutingProviderError):
    """The connection to the provider timed out."""


def find_truck_routes(origin: Coordinates, destination: Coordinates, api_key: str) -> list[Route]:
    """Return distinct truck routes in provider order."""
    locations = f"{_location(origin)}:{_location(destination)}"
    query = urlencode({
        "key": api_key,
        "travelMode": "truck",
        "vehicleCommercial": "true",
        "maxAlternatives": _MAX_ROUTES - 1,
        "alternativeType": "anyRoute",
        "routeRepresentation": "polyline",
        "sectionType": "travelMode",
    })
    try:
        with urlopen(f"{_BASE_URL}/{locations}/json?{query}", timeout=_TIMEOUT_S) as response:
            return _parse_routes(_read_json(response))
    except HTTPError as error:
        with error:
            try:
                code = _error_code(error)
            except TimeoutError:
                raise RoutingTimeout("Routing request timed out") from None
            if error.code == 400 and code in _NO_ROUTE_CODES:
                return []
            if error.code == 504 or code == "COMPUTE_TIME_LIMIT_EXCEEDED":
                raise RoutingTimeout("Routing request timed out") from None
        raise RoutingProviderError("Routing request failed") from None
    except TimeoutError:
        raise RoutingTimeout("Routing request timed out") from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise RoutingTimeout("Routing request timed out") from None
        raise RoutingProviderError("Routing request failed") from None
    except (OSError, HTTPException, ValueError, KeyError, TypeError, OverflowError):
        raise RoutingProviderError("Routing provider returned an unusable response") from None


def _location(position: Coordinates) -> str:
    latitude = format(Decimal(str(position.latitude)), "f")
    longitude = format(Decimal(str(position.longitude)), "f")
    return f"{latitude},{longitude}"


def _read_json(response) -> dict:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Routing response exceeded the size limit")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a routing response object")
    return data


def _error_code(response: HTTPError) -> str | None:
    try:
        code = _read_json(response)["detailedError"]["code"]
        return code if isinstance(code, str) else None
    except TimeoutError:
        raise
    except (OSError, HTTPException, ValueError, KeyError, TypeError):
        return None


def _parse_routes(data: dict) -> list[Route]:
    candidates = data["routes"]
    if not isinstance(candidates, list):
        raise ValueError("Expected a route list")

    routes = []
    seen = set()
    for candidate in candidates:
        sections = candidate["sections"]
        if not isinstance(sections, list):
            raise ValueError("Expected a section list")
        modes = [section["travelMode"] for section in sections if section["sectionType"] == "TRAVEL_MODE"]
        if not modes:
            raise ValueError("Expected truck travel mode sections")
        if any(mode != "truck" for mode in modes):
            continue

        geometry = _route_geometry(candidate["legs"])
        if geometry in seen:
            continue

        summary = candidate["summary"]
        routes.append(Route(
            route_id=f"route-{len(routes) + 1}",
            geometry=geometry,
            distance_m=_number(summary["lengthInMeters"]),
            duration_s=_number(summary["travelTimeInSeconds"]),
        ))
        seen.add(geometry)
        if len(routes) == _MAX_ROUTES:
            break
    return routes


def _route_geometry(legs: list) -> tuple[Coordinates, ...]:
    geometry = []
    for leg in legs:
        for point in leg["points"]:
            position = Coordinates(_number(point["latitude"]), _number(point["longitude"]))
            if not geometry or geometry[-1] != position:
                geometry.append(position)
    return tuple(geometry)


def _number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("Expected a numeric routing response field")
    return float(value)
