"""ATProto OAuth client HTTP endpoints.

Task 2 lands the published `client-metadata.json`. The login + callback views
arrive in Task 3.
"""

from django.http import JsonResponse

from . import config


def client_metadata(request):
    """Serve the ATProto OAuth client metadata at the `client_id` URL."""
    return JsonResponse(config.client_metadata())
