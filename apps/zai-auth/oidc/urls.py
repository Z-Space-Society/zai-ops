"""OIDC provider + JWKS routes.

JWKS lands in Task 2; discovery/authorize/token in Task 4.
"""

from django.urls import path

from . import views

app_name = "oidc"

urlpatterns = [
    path(".well-known/jwks.json", views.jwks, name="jwks"),
]
