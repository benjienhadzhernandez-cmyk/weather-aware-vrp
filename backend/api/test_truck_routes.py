import io
import json
from ssl import SSLCertVerificationError
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings


def _candidate(midpoint, *, travel_mode="truck"):
    return {
        "summary": {"lengthInMeters": 435_000, "travelTimeInSeconds": 16_200},
        "legs": [{"points": [
            {"latitude": 34.05, "longitude": -118.25},
            {"latitude": midpoint, "longitude": -117.5},
            {"latitude": 36.17, "longitude": -115.14},
        ]}],
        "sections": [{"sectionType": "TRAVEL_MODE", "travelMode": travel_mode}],
    }


@override_settings(TOMTOM_API_KEY="test-key")
class TruckRouteTests(SimpleTestCase):
    url = "/api/routes/?origin=34.05,-118.25&destination=36.17,-115.14"

    @patch("api.routing_provider.urlopen")
    def test_requests_truck_alternatives_and_returns_at_most_three_distinct_routes(self, open_url):
        open_url.return_value = io.BytesIO(json.dumps({"routes": [
            _candidate(34.5), _candidate(34.5), _candidate(34.6, travel_mode="car"),
            _candidate(34.7), _candidate(34.8), _candidate(34.9),
        ]}).encode())

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        routes = response.json()["routes"]
        self.assertEqual([route["route_id"] for route in routes], ["route-1", "route-2", "route-3"])
        self.assertEqual([route["geometry"][1]["latitude"] for route in routes], [34.5, 34.7, 34.8])
        self.assertEqual(routes[0]["distance_m"], 435_000)
        self.assertEqual(routes[0]["duration_s"], 16_200)
        url, = open_url.call_args.args
        request = urlsplit(url)
        self.assertEqual(request.netloc, "api.tomtom.com")
        self.assertEqual(request.path, "/routing/1/calculateRoute/34.05,-118.25:36.17,-115.14/json")
        self.assertEqual(parse_qs(request.query), {
            "key": ["test-key"], "travelMode": ["truck"], "vehicleCommercial": ["true"],
            "maxAlternatives": ["2"], "alternativeType": ["anyRoute"],
            "routeRepresentation": ["polyline"], "sectionType": ["travelMode"],
        })
        self.assertGreater(open_url.call_args.kwargs["timeout"], 0)

    @patch("api.routing_provider.urlopen")
    def test_returns_fewer_routes_when_others_require_non_truck_segments(self, open_url):
        partial = _candidate(34.7)
        partial["sections"].append({"sectionType": "TRAVEL_MODE", "travelMode": "other"})
        open_url.return_value = io.BytesIO(json.dumps({"routes": [_candidate(34.5), partial]}).encode())

        self.assertEqual(len(self.client.get(self.url).json()["routes"]), 1)

    @patch("api.routing_provider.urlopen")
    def test_rejects_bad_coordinates_before_calling_provider(self, open_url):
        for query in (
            "", "?origin=34.05,-118.25", "?origin=34,35&origin=34,36&destination=0,1",
            "?origin=NaN,0&destination=0,1", "?origin=0,0&destination=0,181",
            "?origin=0,0&destination=0,0", "?origin=0,0,0&destination=0,1",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/api/routes/{query}").status_code, 400)
        open_url.assert_not_called()

    @override_settings(TOMTOM_API_KEY="")
    @patch("api.routing_provider.urlopen")
    def test_returns_service_unavailable_without_a_key(self, open_url):
        self.assertEqual(self.client.get(self.url).status_code, 503)
        open_url.assert_not_called()

    @patch("api.routing_provider.urlopen")
    def test_returns_empty_list_for_provider_no_route(self, open_url):
        body = io.BytesIO(b'{"detailedError":{"code":"NO_ROUTE_FOUND","message":"private"}}')
        open_url.side_effect = HTTPError("https://api.tomtom.com/?key=test-key", 400, "error", {}, body)

        self.assertEqual(self.client.get(self.url).json(), {"routes": []})

    @patch("api.routing_provider.urlopen")
    def test_sanitizes_upstream_errors(self, open_url):
        open_url.side_effect = HTTPError("https://api.tomtom.com/?key=test-key", 403, "private", {}, None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"error": "Truck routing provider is unavailable"})

    @patch("api.routing_provider.urlopen", side_effect=URLError(TimeoutError()))
    def test_returns_gateway_timeout(self, _open_url):
        self.assertEqual(self.client.get(self.url).status_code, 504)

    @patch("api.routing_provider.urlopen", side_effect=URLError(SSLCertVerificationError(1, "private CA detail")))
    def test_reports_certificate_verification_failure_without_exposing_details(self, _open_url):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"error": "Could not verify the truck routing provider's TLS certificate"})

    @patch("api.routing_provider.urlopen")
    def test_returns_gateway_timeout_for_provider_compute_limit(self, open_url):
        body = io.BytesIO(b'{"detailedError":{"code":"COMPUTE_TIME_LIMIT_EXCEEDED"}}')
        open_url.side_effect = HTTPError("https://api.tomtom.com/?key=test-key", 400, "error", {}, body)

        self.assertEqual(self.client.get(self.url).status_code, 504)

    @patch("api.routing_provider.urlopen")
    def test_returns_gateway_timeout_when_provider_error_body_stalls(self, open_url):
        body = io.BytesIO()
        open_url.side_effect = HTTPError("https://api.tomtom.com/?key=test-key", 400, "error", {}, body)

        with patch.object(body, "read", side_effect=TimeoutError):
            self.assertEqual(self.client.get(self.url).status_code, 504)

    @patch("api.routing_provider.urlopen")
    def test_rejects_a_malformed_upstream_route(self, open_url):
        candidate = _candidate(34.5)
        candidate["summary"]["lengthInMeters"] = True
        open_url.return_value = io.BytesIO(json.dumps({"routes": [candidate]}).encode())

        self.assertEqual(self.client.get(self.url).status_code, 502)
