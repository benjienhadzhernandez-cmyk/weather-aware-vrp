# Weather-Aware Truck Routing

React and Django application for comparing truck routes using forecast weather, cargo weight, and travel time.

## Structure

- `frontend/`: React + TypeScript (Vite)
- `backend/`: Django API

## Prerequisites

- Node.js 20+ and npm
- Python 3.11+ and pip

## Run locally (PowerShell)

From the project root, create `.env` if it is missing.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Open `.env` and set `TOMTOM_API_KEY` to a key with TomTom Routing API access. The file also supports optional Open-Meteo and Django settings. Existing environment variables take precedence. Git ignores `.env`.

In the backend terminal, create and install the virtual environment, then start Django.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

In a second terminal opened at the project root, install the frontend dependencies and start Vite.

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

Open the URL printed by Vite, usually `http://localhost:5173/`. Leave both servers running. Vite proxies `/api` to Django at port 8000.

In RouteWise, choose **Use example** to fill a Los Angeles to Las Vegas trip, then choose **Compare routes**. Select a route and move the checkpoint slider to inspect its forecast. You can also enter your own coordinates as latitude and longitude in decimal degrees. The departure input uses the device's time zone and rejects local times that are skipped or repeated during a clock change. The OpenStreetMap basemap is optional for viewing the route lines and checkpoint data.

### Verify the local setup

Run these commands from the project root.

```powershell
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe backend\manage.py test api
npm.cmd --prefix frontend run build
```

With Django running, check the health endpoint and the example trip.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/health/'
$trip = Invoke-RestMethod 'http://127.0.0.1:8000/api/recommendations/?origin=34.05,-118.25&destination=36.17,-115.14&cargo_weight_kg=5000'
$trip.recommendation_status
$trip.routes | Format-Table route_id, rank, weather_coverage, scored_checkpoints, checkpoint_count
```

The live example returned three truck routes and a weather-based recommendation on November 24, 2026. Route options, forecast coverage, and the recommendation vary with provider data and departure time.

### Troubleshoot local requests

- If the frontend reports **API unavailable**, run the health request above. Check that Django is listening on port 8000 and that the browser is open to the Vite URL.
- If routing returns `503`, confirm `.env` is in the project root, check that `TOMTOM_API_KEY` is set, and restart Django. Check whether a Windows environment variable is overriding the file.
- If routing returns `502` with a provider TLS certificate message, check the machine clock, update your Python and system trust configuration, and check whether your network uses an HTTPS inspection proxy. If your network administrator supplies a trusted CA bundle, set the backend session's `SSL_CERT_FILE` to its PEM path before starting Django. Keep certificate verification enabled.
- For other `502` errors, check that the configured key has Routing API access and that the server can reach the provider named in the error. For a `504`, retry after the provider responds again.
- If routes are present with incomplete weather, inspect their scored checkpoint counts. Routes missing any checkpoint risk remain unranked.

The `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS` defaults are for local development. Configure the deployment environment with debug mode disabled, a strong secret, and the intended hostnames.

## Truck route options

From a second terminal, send origin and destination as `latitude,longitude` in signed decimal degrees.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/routes/?origin=34.05,-118.25&destination=36.17,-115.14' |
    ConvertTo-Json -Depth 6
```

The `routes` array contains up to three distinct options in provider order. Each option includes a `route_id` scoped to the response, `distance_m`, `duration_s`, and ordered `geometry` points with `latitude` and `longitude`. Fewer options are possible when the provider finds fewer eligible alternatives. A valid lookup with zero eligible options returns `200` with an empty array. Invalid coordinates return `400`, a missing provider key returns `503`, and provider errors or timeouts return `502` or `504`.

The backend requests TomTom commercial truck routes and excludes options that TomTom marks with any non-truck travel section, including off-network access. This version uses the general truck profile without vehicle-specific dimensions or weight. Restrictions depend on map coverage and the information supplied to the provider. Check posted limits against the vehicle before driving. See the [TomTom Calculate Route documentation](https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/calculate-route).

## Weather at route checkpoints

The backend's `assess_route_weather` function samples both route endpoints and spaces interior checkpoints along the route geometry. It aims for 50 km between checkpoints and uses at most 12 per route. Checkpoint distances and times are proportional to the route provider's total distance and estimated duration. These estimates assume uniform progress and exclude stop times.

Forecasts come from the [Open-Meteo Forecast API](https://open-meteo.com/en/docs). Submit a timezone-aware departure time. The backend evaluates each checkpoint at the first UTC hour at or after its estimated arrival. That hour includes the preceding hour's total precipitation and maximum gust. Arrival times up to one minute before the assessment clock remain eligible so a just-submitted departure keeps its origin checkpoint. Older arrivals, arrivals beyond the seven-day UTC forecast window, and absent forecast hours have no score. A checkpoint also stays unscored when a required measurement is missing. Open-Meteo supplies the latest available forecast. The score uses no historical fallback.

The public forecast endpoint supports noncommercial evaluation. For commercial use, set a subscribed `OPEN_METEO_API_KEY` in the Django server's environment to use the dedicated customer endpoint. See [Open-Meteo pricing](https://open-meteo.com/en/pricing).

### Checkpoint risk score

Each component ramps linearly across its stated range and stops increasing at its maximum. The total is capped at 100.

- Wind contributes up to 45 base points as sustained speed or gusts rise from 10 to 30 m/s. Cargo weight multiplies the wind component by 1.35 for an empty load, tapering to 1.0 at 20,000 kg.
- Precipitation contributes up to 30 points as hourly precipitation rises from 0 to 20 mm.
- Low visibility contributes up to 25 points as visibility drops from 2,000 to 200 meters.
- Possible icing adds 20 points when the temperature is at or below 1 °C and precipitation is present.

These thresholds are planning heuristics. Wind direction, vehicle dimensions, total vehicle weight, and surface temperature are outside this score.

## Route recommendations

`GET /api/recommendations/` accepts the same `origin` and `destination` parameters as `/api/routes/`, plus required `cargo_weight_kg` and optional `departure_at`. Cargo weight must be a finite, nonnegative number in kilograms. Omit departure time to use the current time, or send an ISO 8601 date and time with `Z` or a URL-encoded UTC offset. Departure can be up to seven days ahead. The API accepts a one-minute grace period for immediate departures and uses future departures to calculate TomTom travel times. Checkpoints outside the available forecast window remain unscored.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/recommendations/?origin=34.05,-118.25&destination=36.17,-115.14&cargo_weight_kg=5000' |
    ConvertTo-Json -Depth 8
```

Only routes with a risk score at every sampled checkpoint are eligible for ranking. The API orders eligible routes by lowest peak risk, then lowest mean risk, then shortest estimated travel time. The response displays the mean rounded to one decimal. TomTom order breaks exact ties. The recommendation is the first eligible route. Routes with incomplete weather follow in provider order and retain their checkpoint results without a rank or route-level score.

- The response includes `departure_at`, `cargo_weight_kg`, `recommended_route_id`, `recommendation_status`, `weather_attribution`, and `routes`. The route ID is `null` when the status is `no_routes` or `insufficient_weather`.
- Each route includes the fields returned by `/api/routes/`, plus `rank`, `weather_coverage` (`complete`, `partial`, or `unavailable`), `checkpoint_count`, `scored_checkpoints`, `peak_risk_score`, `mean_risk_score`, and `checkpoints`.
- Checkpoints include `position`, `distance_from_origin_m`, `elapsed_s`, `arrival_at`, `forecast`, and `risk`. Forecast values use the units in their field names and include `valid_at`. A missing forecast or score is `null`. A scored checkpoint has a numeric `score` and a list of contributing `factors`.
- Validation errors return `400`, missing TomTom configuration returns `503`, and a failed routing or weather provider request returns `502` or `504` for timeouts. The routing-only endpoint keeps its existing response format.

Forecast data by [Open-Meteo](https://open-meteo.com/) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The application selects hourly samples for checkpoint arrivals and computes its own risk scores.
