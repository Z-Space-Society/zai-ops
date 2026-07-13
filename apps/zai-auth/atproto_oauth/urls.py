"""ATProto OAuth client routes."""

from django.urls import path

from . import views

app_name = "atproto_oauth"

urlpatterns = [
    path("client-metadata.json", views.client_metadata, name="client_metadata"),
    path("login", views.login, name="login"),
    path("logout", views.logout, name="logout"),
    path("oauth/callback", views.callback, name="callback"),
    path("", views.landing, name="landing"),
]
