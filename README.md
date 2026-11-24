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

## Initial scope

This foundation provides the React interface, Django API, and a health check. Routing, weather integrations, checkpoint sampling, risk scoring, route ranking, mapping, and forecast visualization are planned for later steps.
