"""ATProto OAuth client routes.

client-metadata.json lands in Task 2; login + callback in Task 3 (the `callback`
route is declared now so `config.redirect_uri()` can `reverse()` it).
"""

from django.urls import path

from . import views

app_name = "atproto_oauth"

urlpatterns = [
    path("client-metadata.json", views.client_metadata, name="client_metadata"),
    path("oauth/callback", views.client_metadata, name="callback"),  # replaced in Task 3
]
