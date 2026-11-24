"""Open-Meteo hourly forecasts for checkpoints at estimated arrival times."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from .routing_models import Checkpoint, Coordinates, WeatherForecast

_PUBLIC_URL = "https://api.open-meteo.com/v1/forecast"
_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"
_FIELDS = {
    "temperature_c": "temperature_2m",
    "precipitation_mm_per_h": "precipitation",
    "wind_speed_m_per_s": "wind_speed_10m",
    "wind_gust_m_per_s": "wind_gusts_10m",
    "visibility_m": "visibility",
}
_FORECAST_DAYS = 7
_ARRIVAL_CLOCK_TOLERANCE = timedelta(minutes=1)
_TIMEOUT_S = 8
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class WeatherProviderError(Exception):
    """The forecast provider could not return a usable response."""


def fetch_checkpoint_forecasts(
    arrivals: Sequence[tuple[Checkpoint, datetime]],
    *,
    now: datetime,
    api_key: str = "",
) -> tuple[WeatherForecast | None, ...]:
    """Fetch one hourly batch and preserve the input order, including missing forecasts."""
    if now.utcoffset() is None:
        raise ValueError("now must include a time zone")
    now = now.astimezone(UTC)
    horizon = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=_FORECAST_DAYS)
    active = []
    for index, (checkpoint, arrival) in enumerate(arrivals):
        if arrival.utcoffset() is None:
            raise ValueError("checkpoint arrival must include a time zone")
        arrival = arrival.astimezone(UTC)
        if now - _ARRIVAL_CLOCK_TOLERANCE <= arrival < horizon:
            forecast_hour = _forecast_hour(arrival)
            if forecast_hour < horizon:
                active.append((index, checkpoint, forecast_hour))

    forecasts: list[WeatherForecast | None] = [None] * len(arrivals)
    if not active:
        return tuple(forecasts)

    positions = list(dict.fromkeys(checkpoint.position for _, checkpoint, _ in active))
    by_position = dict(zip(positions, _load_hourly(positions, api_key), strict=True))
    for index, checkpoint, forecast_hour in active:
        forecasts[index] = _select_forecast(checkpoint, forecast_hour, by_position[checkpoint.position])
    return tuple(forecasts)


def _forecast_hour(arrival: datetime) -> datetime:
    hour = arrival.replace(minute=0, second=0, microsecond=0)
    return hour if arrival == hour else hour + timedelta(hours=1)


def _load_hourly(positions: list[Coordinates], api_key: str) -> list[dict]:
    query = {
        "latitude": ",".join(str(position.latitude) for position in positions),
        "longitude": ",".join(str(position.longitude) for position in positions),
        "hourly": ",".join(_FIELDS.values()),
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "wind_speed_unit": "ms",
        "timezone": "GMT",
        "timeformat": "unixtime",
        "forecast_days": _FORECAST_DAYS,
    }
    if api_key:
        query["apikey"] = api_key
    base_url = _CUSTOMER_URL if api_key else _PUBLIC_URL
    try:
        with urlopen(f"{base_url}?{urlencode(query)}", timeout=_TIMEOUT_S) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("Forecast response exceeded the size limit")
        data = json.loads(raw)
        rows = data if isinstance(data, list) else [data]
        if len(rows) != len(positions):
            raise ValueError("Forecast count did not match the checkpoint request")
        return [_hourly_data(row) for row in rows]
    except HTTPError as error:
        error.close()
        raise WeatherProviderError("Weather provider is unavailable") from None
    except (OSError, HTTPException, ValueError, TypeError, KeyError):
        raise WeatherProviderError("Weather provider is unavailable") from None


def _hourly_data(row: object) -> dict:
    if not isinstance(row, dict) or row.get("error"):
        raise ValueError("Invalid forecast response")
    hourly = row.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Invalid hourly forecast response")
    times = hourly.get("time")
    if not isinstance(times, list) or any(type(value) is not int for value in times):
        raise ValueError("Invalid forecast times")
    if any(later <= earlier for earlier, later in zip(times, times[1:])):
        raise ValueError("Forecast times must be strictly increasing")
    for field in _FIELDS.values():
        values = hourly.get(field)
        if field in hourly and (not isinstance(values, list) or len(values) != len(times)):
            raise ValueError("Forecast fields must align with the hourly times")
    return hourly


def _select_forecast(checkpoint: Checkpoint, hour: datetime, hourly: dict) -> WeatherForecast | None:
    try:
        index = hourly["time"].index(int(hour.timestamp()))
    except ValueError:
        return None

    measurements = {}
    for field, key in _FIELDS.items():
        value = hourly[key][index] if key in hourly else None
        if value is not None and type(value) not in (int, float):
            raise WeatherProviderError("Weather provider is unavailable")
        measurements[field] = value
    if all(value is None for value in measurements.values()):
        return None
    try:
        return WeatherForecast(checkpoint, hour, **measurements)
    except (ValueError, OverflowError):
        raise WeatherProviderError("Weather provider is unavailable") from None
