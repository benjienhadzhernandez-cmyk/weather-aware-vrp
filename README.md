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

The backend supports `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS` environment variables. Defaults are for local development only; configure a strong secret and disable debug mode before deployment.

## Truck route options

Set `TOMTOM_API_KEY` in the backend PowerShell session before starting Django. Use a TomTom key with Routing API access and keep it on the server.

```powershell
$env:TOMTOM_API_KEY = '<your TomTom API key>'
python backend\manage.py runserver 8000
```

From a second terminal, send origin and destination as `latitude,longitude` in signed decimal degrees.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/routes/?origin=34.05,-118.25&destination=36.17,-115.14' |
    ConvertTo-Json -Depth 6
```

The `routes` array contains up to three distinct options in provider order. Each option includes a `route_id` scoped to the response, `distance_m`, `duration_s`, and ordered `geometry` points with `latitude` and `longitude`. Fewer options are possible when the provider finds fewer eligible alternatives. A valid lookup with zero eligible options returns `200` with an empty array. Invalid coordinates return `400`, a missing provider key returns `503`, and provider errors or timeouts return `502` or `504`.

The backend requests TomTom commercial truck routes and excludes options that TomTom marks with any non-truck travel section, including off-network access. This version uses the general truck profile without vehicle-specific dimensions or weight. Restrictions depend on map coverage and the information supplied to the provider. Check posted limits against the vehicle before driving. See the [TomTom Calculate Route documentation](https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/calculate-route).

## Initial scope

The application provides the React interface, Django health check, and truck route options API. Weather integrations, checkpoint sampling, risk scoring, route ranking, mapping, and forecast visualization are planned for later steps.
