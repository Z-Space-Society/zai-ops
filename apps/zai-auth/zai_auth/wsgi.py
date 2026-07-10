"""WSGI entrypoint for ZAI Auth."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zai_auth.settings")

application = get_wsgi_application()

# Fail closed rather than silently serving with a guessable session/CSRF key.
# Only checked here (the real production entrypoint, gunicorn) so `manage.py
# test`/`migrate`/`runserver` keep working out of the box in development —
# see settings.py's SECRET_KEY comment.
from django.conf import settings  # noqa: E402

if not settings.DEBUG and settings.SECRET_KEY == "dev-insecure-key-do-not-use-in-prod":
    raise RuntimeError(
        "SECRET_KEY is unset in production (DEBUG=False) — set it via the "
        "environment before starting the WSGI server."
    )
