import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from .checkpoints import METERS_PER_MILE
from .route_recommendations import rank_routes
from .routing_models import Checkpoint, CheckpointRisk, Coordinates, RiskCategory, Route, WeatherForecast
from .weather_risk import CheckpointAssessment


class RouteRankingTests(SimpleTestCase):
    def test_ranks_by_severe_miles_then_high_miles_then_weighted_average_then_time(self):
        origin, destination = Coordinates(0, 0), Coordinates(0, 1)
        low, moderate, high, severe, no_travel = RiskCategory
        inputs = [
            ("severe", 3_600, (severe, low, low)), ("high", 3_600, (high, low, low)),
            ("moderate-long", 3_600, (moderate, low, low)), ("weighted-slower", 7_200, (low, low, moderate)),
            ("weighted-faster", 5_400, (low, low, moderate)), ("blocked", 1, (None, no_travel, low)),
            ("unknown", 1, (low, None, low)),
        ]
        routes = [Route(name, (origin, destination), 100 * METERS_PER_MILE, duration)
                  for name, duration, _ in inputs]
        now = datetime(2026, 11, 24, 10, tzinfo=UTC)
        assessments = []
        for route, (_, _, categories) in zip(routes, inputs, strict=True):
            rows = []
            for distance, category in zip((0, 80, 100), categories, strict=True):
                point = Checkpoint(route.route_id, origin, distance * METERS_PER_MILE, 0)
                forecast = WeatherForecast(point, now, temperature_c=15)
                risk = CheckpointRisk(forecast, 0, category * 25, category=category) if category is not None else None
                rows.append(CheckpointAssessment(point, now, forecast, risk))
            assessments.append(tuple(rows))

        ranked = rank_routes(routes, assessments)

        self.assertEqual([item.route.route_id for item in ranked],
                         ["weighted-faster", "weighted-slower", "moderate-long", "high", "severe", "blocked", "unknown"])
        self.assertAlmostEqual(ranked[0].average_risk_score, 5)
        self.assertEqual(ranked[3].high_miles, 80)
        self.assertEqual(ranked[4].severe_miles, 80)
        self.assertEqual(ranked[5].no_travel_miles, 100)
        self.assertEqual(ranked[5].risk_category, no_travel)
        self.assertFalse(ranked[5].eligible)
        self.assertEqual(ranked[-1].weather_coverage, "partial")
        self.assertIsNone(ranked[-1].risk_category)


@override_settings(TOMTOM_API_KEY="test-tomtom-key", OPEN_METEO_API_KEY="")
class RouteRecommendationApiTests(SimpleTestCase):
    url = "/api/recommendations/"
    params = {"origin": "34,-118", "destination": "36,-116", "cargo_weight_kg": "5000"}
    now = datetime(2026, 11, 24, 10, tzinfo=UTC)

    def setUp(self):
        self.clock = self.enterContext(patch("api.views.datetime", wraps=datetime))
        self.clock.now.return_value = self.now
        self.routing = self.enterContext(patch("api.routing_provider.urlopen"))
        self.weather = self.enterContext(patch("api.weather_provider.urlopen"))

    def _tomtom_response(self, *midpoints, length_m=100_000):
        self.routing.return_value = _response({"routes": [{
            "summary": {"lengthInMeters": length_m[index] if isinstance(length_m, tuple) else length_m,
                        "travelTimeInSeconds": 7_200},
            "legs": [{"points": [
                {"latitude": 34, "longitude": -118},
                {"latitude": latitude, "longitude": -117},
                {"latitude": 36, "longitude": -116},
            ]}],
            "sections": [{"sectionType": "TRAVEL_MODE", "travelMode": "truck"}],
        } for index, latitude in enumerate(midpoints)]})

    def _weather_row(self, gust, *, start_hour=0):
        hours = [int((self.now + timedelta(hours=offset)).timestamp()) for offset in range(start_hour, start_hour + 3)]
        return {"hourly": {
            "time": hours, "temperature_2m": [15] * 3, "precipitation": [0] * 3,
            "wind_speed_10m": [5] * 3, "wind_gusts_10m": [gust] * 3, "visibility": [10_000] * 3,
            "rain": [0] * 3, "showers": [0] * 3, "snowfall": [0] * 3,
        }}

    def test_ranks_eligible_options_and_keeps_partial_option_unranked(self):
        self._tomtom_response(34.6, 35, 35.3)
        self.weather.return_value = _response([self._weather_row(value) for value in (5, 21, 5, 15, None)])

        response = self.client.get(self.url, self.params)

        self.assertEqual(response.status_code, 200)
        self.routing.assert_called_once()
        self.weather.assert_called_once()
        self.assertNotIn("departAt", parse_qs(urlsplit(self.routing.call_args.args[0]).query))
        body = response.json()
        self.assertEqual(body["recommendation_status"], "recommended")
        self.assertEqual(body["recommended_route_id"], "route-2")
        self.assertEqual(body["weather_attribution"]["name"], "Open-Meteo")
        self.assertEqual(body["departure_at"], self.now.isoformat())
        self.assertEqual(body["interval_miles"], 50)
        self.assertEqual([route["rank"] for route in body["routes"]], [1, 2, None])
        self.assertEqual([route["weather_coverage"] for route in body["routes"]], ["complete", "complete", "partial"])
        partial = body["routes"][-1]
        self.assertEqual(partial["scored_checkpoints"], 2)
        self.assertEqual(partial["checkpoint_count"], 3)
        self.assertIsNone(partial["peak_risk_score"])
        self.assertIsNone(partial["checkpoints"][1]["risk"])
        self.assertIsNone(partial["checkpoints"][1]["forecast"]["wind_gust_m_per_s"])
        self.assertEqual(body["routes"][0]["checkpoints"][0]["arrival_at"], self.now.isoformat())
        self.assertEqual(body["routes"][0]["checkpoints"][1]["distance_from_origin_m"], 50 * METERS_PER_MILE)
        self.assertEqual(body["routes"][0]["corridor_forecast"][0]["checkpoint_risk_categories"], ["low", "moderate", "low"])
        self.assertEqual(len(body["routes"][0]["corridor_forecast"]), 49)
        self.assertEqual(body["routes"][0]["corridor_forecast"][48]["risk_category"], None)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_uses_future_departure_for_both_providers(self):
        self._tomtom_response(35)
        self.weather.return_value = _response([self._weather_row(0, start_hour=2)] * 3)

        response = self.client.get(self.url, {**self.params, "departure_at": "2026-11-24T04:00:00-08:00"})

        self.assertEqual(response.status_code, 200)
        departure = (self.now + timedelta(hours=2)).isoformat()
        query = parse_qs(urlsplit(self.routing.call_args.args[0]).query)
        self.assertEqual(query["departAt"], [departure])
        body = response.json()
        self.assertEqual(body["departure_at"], departure)
        self.assertEqual(body["routes"][0]["checkpoints"][0]["forecast"]["valid_at"], departure)
        self.assertEqual(body["routes"][0]["corridor_forecast"][0]["valid_at"], self.now.isoformat())
        self.assertEqual(body["routes"][0]["corridor_forecast"][2]["valid_at"], departure)

    def test_withholds_recommendation_when_every_route_has_unknown_weather(self):
        self._tomtom_response(35)
        self.weather.return_value = _response([self._weather_row(None)] * 3)

        body = self.client.get(self.url, self.params).json()

        self.assertEqual(body["recommendation_status"], "insufficient_weather")
        self.assertIsNone(body["recommended_route_id"])
        self.assertIsNone(body["routes"][0]["rank"])
        self.assertEqual(body["routes"][0]["weather_coverage"], "unavailable")

    def test_no_route_skips_weather(self):
        self._tomtom_response()

        body = self.client.get(self.url, self.params).json()

        self.assertEqual(body["recommendation_status"], "no_routes")
        self.assertEqual(body["routes"], [])
        self.assertIsNone(body["recommended_route_id"])
        self.weather.assert_not_called()

    def test_no_travel_has_no_recommendation(self):
        self._tomtom_response(35)
        self.weather.return_value = _response([self._weather_row(30)] * 3)

        body = self.client.get(self.url, self.params).json()

        self.assertEqual(body["recommendation_status"], "no_travel")
        self.assertIsNone(body["recommended_route_id"])
        self.assertIsNone(body["routes"][0]["rank"])
        self.assertEqual(body["routes"][0]["risk_category"], "no_travel")
        self.assertAlmostEqual(body["routes"][0]["no_travel_miles"], 100_000 / METERS_PER_MILE)

    def test_rejects_an_oversize_route_before_requesting_weather(self):
        self._tomtom_response(35, length_m=1_201 * METERS_PER_MILE)

        response = self.client.get(self.url, {**self.params, "interval_miles": "10"})

        self.assertEqual(response.status_code, 422)
        self.weather.assert_not_called()

    def test_keeps_other_routes_when_one_exceeds_the_interval_limit(self):
        self._tomtom_response(34.6, 35, length_m=(1_201 * METERS_PER_MILE, 9 * METERS_PER_MILE))
        self.weather.return_value = _response([self._weather_row(5)] * 2)

        response = self.client.get(self.url, {**self.params, "interval_miles": "10"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["recommended_route_id"], "route-2")
        skipped = body["routes"][1]
        self.assertEqual(skipped["route_id"], "route-1")
        self.assertIsNone(skipped["rank"])
        self.assertIn("wider interval", skipped["assessment_error"])
        self.assertEqual(skipped["checkpoint_count"], 0)
        self.assertEqual(skipped["corridor_forecast"], [])

    def test_invalid_parameters_do_not_call_providers(self):
        for invalid in (
            {"cargo_weight_kg": "-1"}, {"cargo_weight_kg": "inf"}, {"cargo_weight_kg": ["1", "2"]},
            {"departure_at": "2026-11-24T11:00:00"}, {"departure_at": "2026-11-24T09:58:00Z"},
            {"departure_at": "2026-12-02T00:00:00Z"}, {"departure_at": "9999-12-31T23:59:00-01:00"},
            {"interval_miles": "15"}, {"interval_miles": ["10", "25"]},
        ):
            with self.subTest(invalid=invalid):
                response = self.client.get(self.url, {**self.params, **invalid})
                self.assertEqual(response.status_code, 400)
        self.routing.assert_not_called()
        self.weather.assert_not_called()

    def test_provider_failures_return_sanitized_errors(self):
        for error, expected_status in ((URLError(TimeoutError()), 504), (URLError("internal detail"), 502)):
            with self.subTest(status=expected_status):
                self._tomtom_response(35)
                self.weather.side_effect = error

                response = self.client.get(self.url, self.params)

                self.assertEqual(response.status_code, expected_status)
                self.assertNotContains(response, "internal detail", status_code=expected_status)


def _response(data):
    return io.BytesIO(json.dumps(data).encode())
