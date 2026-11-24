"""Settings for the Weather-Aware Truck Routing API."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TOMTOM_API_KEY = os.environ.get("TOMTOM_API_KEY", "").strip()
OPEN_METEO_API_KEY = os.environ.get("OPEN_METEO_API_KEY", "").strip()
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-development-only-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.staticfiles", "api"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES: list[dict] = []
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
