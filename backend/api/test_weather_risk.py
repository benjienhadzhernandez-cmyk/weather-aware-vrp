import io
import json
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from .checkpoints import METERS_PER_MILE, CheckpointLimitError, sample_checkpoints
from .routing_models import Checkpoint, Coordinates, RiskCategory, Route, WeatherForecast
from .weather_provider import WeatherProviderError, fetch_checkpoint_forecasts
from .weather_risk import assess_route_weather, score_checkpoint


def _hourly(times, **fields):
    columns = {
        "temperature_2m": [10] * len(times),
        "precipitation": [0] * len(times),
        "wind_speed_10m": [0] * len(times),
        "wind_gusts_10m": [0] * len(times),
        "visibility": [10_000] * len(times),
        "rain": [0] * len(times),
        "showers": [0] * len(times),
        "snowfall": [0] * len(times),
    }
    return {"hourly": {"time": [int(time.timestamp()) for time in times], **columns, **fields}}


def _response(data):
    return io.BytesIO(json.dumps(data).encode())


class CheckpointSamplingTests(SimpleTestCase):
    def test_interpolates_along_geometry_and_preserves_provider_totals(self):
        origin = Coordinates(0, 0)
        turn = Coordinates(0, 1)
        destination = Coordinates(2, 1)
        route = Route("route-1", (origin, turn, turn, destination), 150 * METERS_PER_MILE, 10_800)

        checkpoints = sample_checkpoints(route)

        self.assertEqual(len(checkpoints), 4)
        self.assertEqual(checkpoints[0].position, origin)
        self.assertEqual(checkpoints[-1].position, destination)
        self.assertAlmostEqual(checkpoints[1].position.latitude, turn.latitude)
        self.assertAlmostEqual(checkpoints[1].position.longitude, turn.longitude)
        self.assertAlmostEqual(checkpoints[2].position.latitude, 1)
        self.assertEqual([point.elapsed_s for point in checkpoints], [0, 3_600, 7_200, 10_800])
        self.assertEqual(checkpoints[-1].distance_from_origin_m, route.distance_m)

    def test_keeps_exact_spacing_at_the_antimeridian_and_appends_short_tail(self):
        route = Route("route-1", (Coordinates(0, 179), Coordinates(0, -179)), 120 * METERS_PER_MILE, 30_000)

        checkpoints = sample_checkpoints(route, 25)

        self.assertEqual([point.distance_from_origin_m for point in checkpoints],
                         [miles * METERS_PER_MILE for miles in (0, 25, 50, 75, 100, 120)])
        self.assertTrue(all(abs(point.position.longitude) >= 179 for point in checkpoints))

    def test_rejects_oversize_spacing_instead_of_changing_it(self):
        route = Route("route-1", (Coordinates(0, 0), Coordinates(0, 1)), 1_201 * METERS_PER_MILE, 30_000)
        with self.assertRaisesRegex(CheckpointLimitError, "wider interval"):
            sample_checkpoints(route, 10)
        self.assertLess(len(sample_checkpoints(route, 25)), 121)


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
            _hourly([self.now], wind_gusts_10m=[None]), _hourly([self.now]),
        ])

        origin, destination = assess_route_weather(self.route, self.now, 5_000, now=self.now)

        self.assertIsNotNone(origin.forecast)
        self.assertIsNone(origin.forecast.wind_gust_m_per_s)
        self.assertIsNone(origin.risk)
        self.assertIsNone(destination.forecast)
        self.assertIsNone(destination.risk)

    @patch("api.weather_provider.urlopen")
    def test_provider_uses_rain_and_snow_depth_with_distinct_units(self, open_url):
        hours = [self.now, self.now + timedelta(hours=1)]
        weather = _hourly(hours, rain=[2.54, 0], showers=[3.81, 0], snowfall=[0, 5.08])
        open_url.return_value = _response([weather, weather])

        origin, destination = assess_route_weather(self.route, self.now, 0, now=self.now)

        self.assertAlmostEqual(origin.forecast.rain_in_per_h, 0.25)
        self.assertEqual(origin.risk.category, RiskCategory.HIGH)
        self.assertEqual(origin.risk.factors, ("rain",))
        self.assertEqual(destination.forecast.snowfall_in_per_h, 2.0)
        self.assertEqual(destination.risk.category, RiskCategory.SEVERE)
        self.assertEqual(destination.risk.factors, ("snow",))

    def test_risk_thresholds_and_strict_load_overrides(self):
        point = Checkpoint("route-1", self.route.geometry[0], 0, 0)
        for wind, rain, snow, weight, expected in (
            (24.999, 0, 0, 0, RiskCategory.LOW), (25, 0, 0, 0, RiskCategory.MODERATE),
            (35, 0, 0, 40_000, RiskCategory.HIGH), (35, 0, 0, 40_001, RiskCategory.SEVERE),
            (45, 0, 0, 30_000, RiskCategory.SEVERE), (45, 0, 0, 30_001, RiskCategory.NO_TRAVEL),
            (55, None, None, 0, RiskCategory.NO_TRAVEL), (0, 0.10, 0, 0, RiskCategory.MODERATE),
            (0, 0.25, 0, 0, RiskCategory.HIGH), (0, 0.50, 0, 0, RiskCategory.SEVERE),
            (0, 1.0, 0, 0, RiskCategory.SEVERE), (0, 1.01, 0, 0, RiskCategory.NO_TRAVEL),
            (0, 0, 0.5, 0, RiskCategory.MODERATE), (0, 0, 1.0, 0, RiskCategory.HIGH),
            (0, 0, 2.0, 0, RiskCategory.SEVERE), (0, 0, 3.0, 0, RiskCategory.SEVERE),
            (0, 0, 3.01, 0, RiskCategory.NO_TRAVEL), (0, 0, None, 0, None),
        ):
            with self.subTest(wind=wind, rain=rain, snow=snow, weight=weight):
                forecast = WeatherForecast(point, self.now, wind_speed_m_per_s=0, wind_gust_m_per_s=wind * 0.44704,
                                           rain_in_per_h=rain, snowfall_in_per_h=snow)
                risk = score_checkpoint(forecast, weight * 0.45359237)
                self.assertEqual(risk.category if risk else None, expected)

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
    def test_splits_large_requests_without_reordering_checkpoints(self, open_url):
        points = [Checkpoint("route-1", Coordinates(latitude, 0), latitude, 0) for latitude in range(34)]

        def weather_for_locations(url, *, timeout):
            query = parse_qs(urlsplit(url).query)
            locations = query["latitude"][0].split(",")
            return _response([_hourly([self.now], temperature_2m=[int(latitude)]) for latitude in locations])

        open_url.side_effect = weather_for_locations
        forecasts = fetch_checkpoint_forecasts([(point, self.now) for point in points], now=self.now)

        self.assertEqual(open_url.call_count, 2)
        self.assertEqual([forecast.temperature_c for forecast in forecasts], list(range(34)))

    @patch("api.weather_provider.urlopen")
    def test_rejects_misaligned_provider_columns(self, open_url):
        open_url.return_value = _response(_hourly([self.now], precipitation=[]))
        point = Checkpoint("route-1", self.route.geometry[0], 0, 0)

        with self.assertRaises(WeatherProviderError):
            fetch_checkpoint_forecasts([(point, self.now)], now=self.now)
