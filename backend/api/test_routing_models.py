from datetime import UTC, datetime
from unittest import TestCase

from api.routing_models import Checkpoint, CheckpointRisk, Coordinates, Route, WeatherForecast


class RoutingModelTests(TestCase):
    origin = Coordinates(34.05, -118.25)
    destination = Coordinates(36.17, -115.14)
    checkpoint = Checkpoint("route-1", origin, 0, 0)
    valid_at = datetime(2026, 11, 24, 19, tzinfo=UTC)

    def test_route_preserves_ordered_geometry_and_distinguishes_alternatives(self) -> None:
        geometry = (self.origin, self.destination)
        first = Route("route-1", geometry, 435_000, 16_200)
        second = Route("route-2", geometry, 455_000, 16_800)

        self.assertEqual(first.geometry, geometry)
        self.assertNotEqual(first.route_id, second.route_id)

    def test_coordinates_and_route_reject_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "latitude"):
            Coordinates(float("nan"), 0)
        with self.assertRaisesRegex(ValueError, "longitude"):
            Coordinates(0, -181)
        with self.assertRaisesRegex(ValueError, "route_id"):
            Route(" ", (self.origin, self.destination), 1, 1)
        with self.assertRaisesRegex(ValueError, "geometry"):
            Route("route-1", (self.origin,), 1, 1)
        with self.assertRaisesRegex(ValueError, "distance_m"):
            Route("route-1", (self.origin, self.destination), float("inf"), 1)

    def test_checkpoint_rejects_negative_travel_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "elapsed_s"):
            Checkpoint("route-1", self.origin, 0, -1)

    def test_forecast_distinguishes_missing_measurements_from_zero(self) -> None:
        forecast = WeatherForecast(
            self.checkpoint, self.valid_at, temperature_c=-5, precipitation_mm_per_h=0
        )

        self.assertEqual(forecast.temperature_c, -5)
        self.assertEqual(forecast.precipitation_mm_per_h, 0)
        self.assertIsNone(forecast.visibility_m)

    def test_forecast_rejects_naive_time_and_invalid_measurements(self) -> None:
        with self.assertRaisesRegex(ValueError, "time zone"):
            WeatherForecast(self.checkpoint, self.valid_at.replace(tzinfo=None))
        with self.assertRaisesRegex(ValueError, "wind_speed_m_per_s"):
            WeatherForecast(self.checkpoint, self.valid_at, wind_speed_m_per_s=-1)

    def test_risk_accepts_empty_cargo_and_rejects_invalid_scores(self) -> None:
        forecast = WeatherForecast(self.checkpoint, self.valid_at, temperature_c=20)

        self.assertEqual(CheckpointRisk(forecast, cargo_weight_kg=0, score=0).score, 0)
        with self.assertRaisesRegex(ValueError, "score"):
            CheckpointRisk(forecast, cargo_weight_kg=0, score=101)
        with self.assertRaisesRegex(ValueError, "cargo_weight_kg"):
            CheckpointRisk(forecast, cargo_weight_kg=-1, score=10)
