# Weather-Aware Truck Routing

React and Django application for comparing truck routes using forecast weather, cargo weight, and travel time.

## Structure

- `frontend/`: React + TypeScript (Vite)
- `backend/`: Django API

## Prerequisites

- Node.js 20+ and npm
- Python 3.11+ and pip

## Run locally (PowerShell)

Backend terminal:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python backend\manage.py runserver 8000
```

Frontend terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open the URL printed by Vite. `/api` requests are proxied to Django at port 8000. The health endpoint is `http://127.0.0.1:8000/api/health/`.

In RouteWise, enter origin and destination as latitude and longitude in decimal degrees and choose **Compare routes**. Select a route in the comparison and move the checkpoint slider to inspect its forecast. The basemap loads tiles from OpenStreetMap. Route lines and checkpoint data remain available on a plain background if tiles cannot load.

The backend supports `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS` environment variables. Defaults are for local development only; configure a strong secret and disable debug mode before deployment.

## Truck route options

If the project root has no `.env`, copy the template:

```powershell
Copy-Item .env.example .env
```

Open `.env`, paste your TomTom Routing API key after `TOMTOM_API_KEY=`, and restart Django. The file also supports optional Open-Meteo and Django settings. Existing environment variables take precedence. Git ignores `.env`.

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
