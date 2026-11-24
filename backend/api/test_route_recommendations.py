import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from .route_recommendations import rank_routes
from .routing_models import Checkpoint, CheckpointRisk, Coordinates, Route, WeatherForecast
from .weather_risk import CheckpointAssessment


class RouteRankingTests(SimpleTestCase):
    def test_ranks_complete_routes_by_peak_mean_and_time(self):
        origin, destination = Coordinates(0, 0), Coordinates(0, 1)
        inputs = [(3600, (80, 0)), (3600, (50, 40)), (7200, (50, 20)), (5400, (50, 20)), (1, (0, None))]
        routes = [Route(str(index), (origin, destination), 75_000, duration)
                  for index, (duration, _) in enumerate(inputs)]
        now = datetime(2026, 11, 24, 10, tzinfo=UTC)
        assessments = []
        for route, (_, scores) in zip(routes, inputs, strict=True):
            point = Checkpoint(route.route_id, origin, 0, 0)
            forecast = WeatherForecast(point, now, temperature_c=15)
            assessments.append(tuple(
                CheckpointAssessment(point, now, forecast, None if score is None else CheckpointRisk(forecast, 0, score))
                for score in scores
            ))

        ranked = rank_routes(routes, assessments)

        self.assertEqual([item.route.route_id for item in ranked], ["3", "2", "1", "0", "4"])
        self.assertEqual(ranked[0].mean_risk_score, 35)
        self.assertEqual(ranked[-1].weather_coverage, "partial")
        self.assertIsNone(ranked[-1].peak_risk_score)

    def test_uses_full_precision_mean_when_display_values_tie(self):
        origin, destination = Coordinates(0, 0), Coordinates(0, 1)
        routes = [Route("faster", (origin, destination), 100_000, 3_600),
                  Route("lower-risk", (origin, destination), 100_000, 7_200)]
        now = datetime(2026, 11, 24, 10, tzinfo=UTC)
        assessments = []
        for route, scores in zip(routes, ((50, 10.1, 0), (50, 10, 0)), strict=True):
            point = Checkpoint(route.route_id, origin, 0, 0)
            forecast = WeatherForecast(point, now, temperature_c=15)
            assessments.append(tuple(
                CheckpointAssessment(point, now, forecast, CheckpointRisk(forecast, 0, score))
                for score in scores
            ))

        ranked = rank_routes(routes, assessments)

        self.assertEqual([item.route.route_id for item in ranked], ["lower-risk", "faster"])
        self.assertEqual([round(item.mean_risk_score, 1) for item in ranked], [20.0, 20.0])


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

    def _tomtom_response(self, *midpoints):
        self.routing.return_value = _response({"routes": [{
            "summary": {"lengthInMeters": 100_000, "travelTimeInSeconds": 7_200},
            "legs": [{"points": [
                {"latitude": 34, "longitude": -118},
                {"latitude": latitude, "longitude": -117},
                {"latitude": 36, "longitude": -116},
            ]}],
            "sections": [{"sectionType": "TRAVEL_MODE", "travelMode": "truck"}],
        } for latitude in midpoints]})

    def _weather_row(self, gust, *, start_hour=0):
        hours = [int((self.now + timedelta(hours=offset)).timestamp()) for offset in range(start_hour, start_hour + 3)]
        return {"hourly": {
            "time": hours, "temperature_2m": [15] * 3, "precipitation": [0] * 3,
            "wind_speed_10m": [5] * 3, "wind_gusts_10m": [gust] * 3, "visibility": [10_000] * 3,
        }}

    def test_ranks_eligible_options_and_keeps_partial_option_unranked(self):
        self._tomtom_response(34.6, 35, 35.3)
        self.weather.return_value = _response([self._weather_row(value) for value in (5, 25, 5, 15, None)])

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
        self.assertEqual([route["rank"] for route in body["routes"]], [1, 2, None])
        self.assertEqual([route["weather_coverage"] for route in body["routes"]], ["complete", "complete", "partial"])
        partial = body["routes"][-1]
        self.assertEqual(partial["scored_checkpoints"], 2)
        self.assertEqual(partial["checkpoint_count"], 3)
        self.assertIsNone(partial["peak_risk_score"])
        self.assertIsNone(partial["checkpoints"][1]["risk"])
        self.assertIsNone(partial["checkpoints"][1]["forecast"]["wind_gust_m_per_s"])
        self.assertEqual(body["routes"][0]["checkpoints"][0]["arrival_at"], self.now.isoformat())
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

    def test_invalid_parameters_do_not_call_providers(self):
        for invalid in (
            {"cargo_weight_kg": "-1"}, {"cargo_weight_kg": "inf"}, {"cargo_weight_kg": ["1", "2"]},
            {"departure_at": "2026-11-24T11:00:00"}, {"departure_at": "2026-11-24T09:58:00Z"},
            {"departure_at": "2026-12-02T00:00:00Z"}, {"departure_at": "9999-12-31T23:59:00-01:00"},
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
