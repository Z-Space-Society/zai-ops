"""OIDC provider + JWKS routes."""

from django.urls import path

from . import views

app_name = "oidc"

urlpatterns = [
    path(".well-known/jwks.json", views.jwks, name="jwks"),
    path(
        ".well-known/openid-configuration",
        views.openid_configuration,
        name="openid_configuration",
    ),
    path("oidc/authorize", views.authorize, name="authorize"),
    path("oidc/token", views.token, name="token"),
]
