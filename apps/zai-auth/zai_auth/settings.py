"""
Django settings for ZAI Auth.

Env-driven (12-factor): every deployment-specific value is read from the
environment so the same code runs locally and on the cluster with no edits.
A local `.env` (git-ignored) supplies values in development; `.env.example`
documents the full set with placeholders. No secrets live in this file.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)

# Read a local .env if present (development convenience; never committed).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)

# --- Core -----------------------------------------------------------------

# SECRET_KEY has an insecure default ONLY so `manage.py` runs out of the box in
# development; any real run sets it from the environment.
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-key-do-not-use-in-prod")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Origins Django trusts for unsafe (POST) requests' CSRF Origin check. Needed
# when served behind an HTTPS proxy/tunnel (e.g. cloudflared in dev): the browser
# sends an `https://` Origin while the app sees the forwarded request as `http`,
# so the tunnel host must be declared trusted explicitly.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# The public HTTPS origin this app is served from. It anchors the OAuth
# `client_id`, the OIDC issuer, and the redirect/JWKS URLs. In local dev it can
# be a tunnel or the localhost development convention (see README).
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://localhost:8000")

# --- Signing keys (consumed from Task 2 onward) ---------------------------
# Paths to PEM private keys, loaded lazily by zai_auth.signing. atproto mandates
# ES256 (P-256) for DPoP + client assertion; the OIDC id_token uses RS256 for
# broad OIDC-client compatibility. Two keys, one JWKS. Never committed.
ATPROTO_EC_PRIVATE_KEY_PATH = env("ATPROTO_EC_PRIVATE_KEY_PATH", default="")
OIDC_RSA_PRIVATE_KEY_PATH = env("OIDC_RSA_PRIVATE_KEY_PATH", default="")

# --- OIDC provider (Task 4) -----------------------------------------------
# The single relying party we issue id_tokens to (Open WebUI). Secret is read
# from the environment; only a placeholder appears in .env.example.
OIDC_CLIENT_ID = env("OIDC_CLIENT_ID", default="open-webui")
OIDC_CLIENT_SECRET = env("OIDC_CLIENT_SECRET", default="")
OIDC_REDIRECT_URIS = env.list("OIDC_REDIRECT_URIS", default=[])

# --- Applications ---------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "atproto_oauth",
    "oidc",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "zai_auth.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "zai_auth.wsgi.application"

# --- Database -------------------------------------------------------------
# Postgres is the target store (CT 102, assumed provisioned). Reached via a
# single connection URL so local and cluster runs differ only by env value.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://localhost:5432/zai_auth",
    ),
}

# --- Auth -----------------------------------------------------------------
# The DID-keyed custom user model. Set BEFORE the first migration — switching
# AUTH_USER_MODEL after migrations exist is painful.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LOGIN_URL = "atproto_oauth:login"

# --- i18n / static --------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Session / cookie hardening -------------------------------------------
# Member sessions are server-side Django sessions (not browser-held JWTs).
SESSION_COOKIE_HTTPONLY = True
# Secure cookies are enforced whenever we're not in DEBUG (i.e. behind TLS).
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
