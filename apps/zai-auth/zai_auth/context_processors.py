"""Template context available on every page (base.html's nav needs this)."""

from django.conf import settings


def ui(request):
    return {"CHAT_URL": settings.CHAT_URL}
