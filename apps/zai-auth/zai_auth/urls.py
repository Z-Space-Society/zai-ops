"""Root URL configuration for ZAI Auth.

App routes are mounted from Task 2 (atproto_oauth: client-metadata + login flow)
and Task 4 (oidc: discovery, JWKS, authorize, token) onward.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("atproto_oauth.urls")),
    path("", include("oidc.urls")),
]
