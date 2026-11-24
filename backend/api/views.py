"""Small operational endpoints for the API."""

from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(_request) -> JsonResponse:
    """Report that the API process is responding."""
    return JsonResponse({"status": "ok", "service": "weather-aware-routing-api"})
