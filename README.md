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

In RouteWise, choose **Use example** to fill a Los Angeles to Las Vegas trip, then choose **Compare routes**. For your own trip, enter coordinates or use the map pickers, enter load weight in pounds, and select a checkpoint interval of 10, 25, or 50 miles. The departure input uses your device's time zone. It rejects local times that repeat or are skipped during a clock change.

Select a route card to fit that route on the map. Click a checkpoint marker or move the checkpoint slider to inspect conditions at its estimated arrival. Use the separate **0–48 hour** corridor forecast slider to compare conditions along the selected route at one forecast hour. Changing that preview does not change the trip departure or recommendation. Segment colors approximate conditions between sampled points. The legend identifies missing data separately.

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
$trip = Invoke-RestMethod 'http://127.0.0.1:8000/api/recommendations/?origin=34.05,-118.25&destination=36.17,-115.14&cargo_weight_kg=5000&interval_miles=25'
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

The backend samples both endpoints and places interior checkpoints at the selected mileage interval. The final segment can be shorter. Checkpoint positions and arrival estimates use distance along the route geometry and the routing provider's total distance and duration. The ETA estimates assume uniform progress and exclude stop times.

The forecast budget is 121 checkpoints per route, allowing up to 1,200, 3,000, or 6,000 miles at the 10-, 25-, or 50-mile interval respectively. An oversized alternative stays visible but unassessed if other routes fit. If every alternative exceeds the limit, the API returns HTTP 422. Choose a wider interval or a shorter trip in that case.

Forecasts come from the [Open-Meteo Forecast API](https://open-meteo.com/en/docs). Submit a timezone-aware departure time. The backend evaluates each checkpoint at the first UTC hour at or after its estimated arrival. That hour includes the preceding hour's total precipitation and maximum gust. Arrival times up to one minute before the assessment clock remain eligible so a just-submitted departure keeps its origin checkpoint. Older arrivals, arrivals beyond the seven-day UTC forecast window, and absent forecast hours have no score. A checkpoint also stays unscored when a required measurement is missing. Open-Meteo supplies the latest available forecast. The score uses no historical fallback.

The public forecast endpoint supports noncommercial evaluation. For commercial use, set a subscribed `OPEN_METEO_API_KEY` in the Django server's environment to use the dedicated customer endpoint. See [Open-Meteo pricing](https://open-meteo.com/en/pricing).

### Checkpoint risk levels

The highest category from wind, rain, and snowfall determines the checkpoint's risk. Wind uses the greater of sustained speed and gusts. Rain includes convective showers. Snowfall measures snow depth, separate from liquid precipitation. Shared rain and snow thresholds start the higher category, with the top **No Travel** thresholds applying strictly above 1 in/h for rain and 3 in/h for snow.

| Measurement | Low | Moderate | High | Severe | No Travel |
| --- | --- | --- | --- | --- | --- |
| Wind (mph) | <25 | 25 to <35 | 35 to <45 | 45 to <55 | ≥55 |
| Rain (in/h) | <0.10 | 0.10 to <0.25 | 0.25 to <0.50 | 0.50 to 1.00 | >1.00 |
| Snow (in/h) | <0.5 | 0.5 to <1.0 | 1.0 to <2.0 | 2.0 to 3.0 | >3.0 |

Wind from 45 to under 55 mph becomes **No Travel** for loads above 30,000 lb. Wind from 35 to under 45 mph becomes **Severe** for loads above 40,000 lb. Wind at or above 55 mph is **No Travel** for any load.

## Route recommendations

`GET /api/recommendations/` accepts the same `origin` and `destination` parameters as `/api/routes/`, plus required `cargo_weight_kg`, optional `interval_miles` (10, 25, or 50, default 50), and optional `departure_at`. Cargo weight must be a finite, nonnegative number in kilograms. Omit departure time to use the current time, or send an ISO 8601 date and time with `Z` or a URL-encoded UTC offset. Departure can be up to seven days ahead. The API accepts a one-minute grace period for immediate departures and uses future departures to calculate TomTom travel times. Checkpoints outside the available forecast window remain unscored.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/recommendations/?origin=34.05,-118.25&destination=36.17,-115.14&cargo_weight_kg=5000' |
    ConvertTo-Json -Depth 8
```

Routes with complete weather coverage and no **No Travel** checkpoint are eligible for ranking. The API orders them by fewest Severe miles, then fewest High miles, then lowest distance-weighted average risk, then shortest estimated travel time. Each segment uses the more severe classification at its endpoints. The average weights the category scores (0, 25, 50, 75, and 100) by segment distance. TomTom order breaks exact ties. Ineligible routes stay visible with their available weather details.

- The response includes `departure_at`, `cargo_weight_kg`, `recommended_route_id`, `recommendation_status`, `weather_attribution`, and `routes`. The route ID is `null` when no route is eligible.
- Route summaries include weather coverage, checkpoint counts, risk category, Severe and High miles, and distance-weighted average risk. Checkpoints include the position, distance from origin, estimated arrival, hourly readings, and risk classification. A missing reading or classification is `null`.
- Each route's `corridor_forecast` provides 49 hourly snapshots, starting at the first UTC forecast hour at or after the request. In each snapshot, `checkpoint_risk_categories` lists the risk categories in the same order as the route's checkpoints.
- Validation errors return `400`, missing TomTom configuration returns `503`, and a failed routing or weather provider request returns `502` or `504` for timeouts. The routing-only endpoint keeps its existing response format.

Forecast data by [Open-Meteo](https://open-meteo.com/) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The application selects hourly samples for checkpoint arrivals and computes its own risk scores.
