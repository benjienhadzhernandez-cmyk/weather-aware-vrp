import io
import json
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from .checkpoints import sample_checkpoints
from .routing_models import Checkpoint, Coordinates, Route
from .weather_provider import WeatherProviderError, fetch_checkpoint_forecasts
from .weather_risk import assess_route_weather


def _hourly(times, **fields):
    columns = {
        "temperature_2m": [10] * len(times),
        "precipitation": [0] * len(times),
        "wind_speed_10m": [0] * len(times),
        "wind_gusts_10m": [0] * len(times),
        "visibility": [10_000] * len(times),
    }
    return {"hourly": {"time": [int(time.timestamp()) for time in times], **columns, **fields}}


def _response(data):
    return io.BytesIO(json.dumps(data).encode())


class CheckpointSamplingTests(SimpleTestCase):
    def test_interpolates_along_geometry_and_preserves_provider_totals(self):
        origin = Coordinates(0, 0)
        turn = Coordinates(0, 1)
        destination = Coordinates(2, 1)
        route = Route("route-1", (origin, turn, turn, destination), 150_000, 10_800)

        checkpoints = sample_checkpoints(route)

        self.assertEqual(len(checkpoints), 4)
        self.assertEqual(checkpoints[0].position, origin)
        self.assertEqual(checkpoints[-1].position, destination)
        self.assertAlmostEqual(checkpoints[1].position.latitude, turn.latitude)
        self.assertAlmostEqual(checkpoints[1].position.longitude, turn.longitude)
        self.assertAlmostEqual(checkpoints[2].position.latitude, 1)
        self.assertEqual([point.elapsed_s for point in checkpoints], [0, 3_600, 7_200, 10_800])
        self.assertEqual(checkpoints[-1].distance_from_origin_m, 150_000)

    def test_caps_checkpoint_count_and_crosses_the_antimeridian(self):
        route = Route("route-1", (Coordinates(0, 179), Coordinates(0, -179)), 1_000_000, 30_000)

        checkpoints = sample_checkpoints(route)

        self.assertEqual(len(checkpoints), 12)
        self.assertTrue(all(abs(point.position.longitude) >= 179 for point in checkpoints))


@override_settings(OPEN_METEO_API_KEY="")
class WeatherRiskTests(SimpleTestCase):
    now = datetime(2026, 11, 24, 10, tzinfo=UTC)
    route = Route("route-1", (Coordinates(34, -118), Coordinates(35, -117)), 50_000, 2_700)

    @patch("api.weather_provider.urlopen")
    def test_assessment_batches_coordinates_and_selects_arrival_hours(self, open_url):
        hours = [self.now, self.now + timedelta(hours=1), self.now + timedelta(hours=2)]
        open_url.return_value = _response([
            _hourly(hours, temperature_2m=[1, 10, 20]),
            _hourly(hours, temperature_2m=[2, 11, 21]),
        ])
        local_timezone = timezone(timedelta(hours=-8))
        departure = (self.now + timedelta(minutes=30)).astimezone(local_timezone)

        origin, destination = assess_route_weather(self.route, departure, 5_000, now=self.now)

        self.assertEqual(origin.arrival_at, self.now + timedelta(minutes=30))
        self.assertEqual(destination.arrival_at, self.now + timedelta(minutes=75))
        self.assertEqual(origin.forecast.valid_at, hours[1])
        self.assertEqual(destination.forecast.valid_at, hours[2])
        self.assertEqual([origin.forecast.temperature_c, destination.forecast.temperature_c], [10, 21])
        self.assertEqual(origin.risk.score, 0)
        open_url.assert_called_once()
        url, = open_url.call_args.args
        self.assertEqual(urlsplit(url).netloc, "api.open-meteo.com")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["latitude"], ["34,35"])
        self.assertEqual(query["longitude"], ["-118,-117"])
        self.assertEqual(query["wind_speed_unit"], ["ms"])
        self.assertEqual(query["timeformat"], ["unixtime"])

    @patch("api.weather_provider.urlopen")
    def test_skips_expired_and_out_of_horizon_arrivals(self, open_url):
        point = Checkpoint("route-1", self.route.geometry[0], 0, 0)
        arrivals = [(point, self.now - timedelta(seconds=61)), (point, self.now + timedelta(days=7))]

        self.assertEqual(fetch_checkpoint_forecasts(arrivals, now=self.now), (None, None))
        open_url.assert_not_called()

    @patch("api.weather_provider.urlopen")
    @patch("api.weather_risk.datetime")
    def test_immediate_departure_keeps_origin_with_default_assessment_clock(self, clock, open_url):
        clock.now.return_value = self.now + timedelta(seconds=1)
        hours = [self.now, self.now + timedelta(hours=1)]
        open_url.return_value = _response([_hourly(hours), _hourly(hours)])

        origin, destination = assess_route_weather(self.route, self.now, 5_000)

        self.assertEqual(origin.forecast.valid_at, hours[0])
        self.assertEqual(destination.forecast.valid_at, hours[1])

    @patch("api.weather_provider.urlopen")
    def test_does_not_substitute_missing_hour_or_measurement(self, open_url):
        open_url.return_value = _response([
            _hourly([self.now], visibility=[None]), _hourly([self.now]),
        ])

        origin, destination = assess_route_weather(self.route, self.now, 5_000, now=self.now)

        self.assertIsNotNone(origin.forecast)
        self.assertIsNone(origin.forecast.visibility_m)
        self.assertIsNone(origin.risk)
        self.assertIsNone(destination.forecast)
        self.assertIsNone(destination.risk)

    @patch("api.weather_provider.urlopen")
    def test_light_cargo_adds_wind_risk_and_extreme_weather_caps_the_score(self, open_url):
        hours = [self.now, self.now + timedelta(hours=1)]
        weather = _hourly(hours, wind_gusts_10m=[20, 40], precipitation=[0, 30],
                          temperature_2m=[10, -1], visibility=[10_000, 50])
        open_url.side_effect = [_response([weather, weather]), _response([weather, weather])]

        light = assess_route_weather(self.route, self.now, 0, now=self.now)
        heavy = assess_route_weather(self.route, self.now, 20_000, now=self.now)

        self.assertGreater(light[0].risk.score, heavy[0].risk.score)
        self.assertEqual(light[0].risk.factors, ("wind",))
        self.assertEqual(light[1].risk.score, 100)
        self.assertIn("possible_icing", light[1].risk.factors)

    @patch("api.weather_provider.urlopen")
    def test_uses_customer_endpoint_and_deduplicates_coordinates(self, open_url):
        open_url.return_value = _response(_hourly([self.now]))
        point = Checkpoint("route-1", self.route.geometry[0], 0, 0)

        forecasts = fetch_checkpoint_forecasts([(point, self.now)] * 2, now=self.now, api_key="test-key")

        self.assertEqual(len(forecasts), 2)
        self.assertEqual(forecasts[0], forecasts[1])
        url, = open_url.call_args.args
        self.assertEqual(urlsplit(url).netloc, "customer-api.open-meteo.com")
        self.assertEqual(parse_qs(urlsplit(url).query)["apikey"], ["test-key"])

    @patch("api.weather_provider.urlopen")
    def test_rejects_misaligned_provider_columns(self, open_url):
        open_url.return_value = _response(_hourly([self.now], precipitation=[]))
        point = Checkpoint("route-1", self.route.geometry[0], 0, 0)

        with self.assertRaises(WeatherProviderError):
            fetch_checkpoint_forecasts([(point, self.now)], now=self.now)
