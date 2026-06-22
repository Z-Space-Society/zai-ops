"""WSGI entrypoint for ZAI Auth."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zai_auth.settings")

application = get_wsgi_application()
