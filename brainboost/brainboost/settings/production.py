from .base import *
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Production: kein Debug
DEBUG = False

# Einmaliges Wartungsfenster; Banner und Sperre enden automatisch.
_maintenance_timezone = ZoneInfo(TIME_ZONE)
MAINTENANCE_MODE_ENABLED = True
MAINTENANCE_START = datetime(2026, 9, 3, 9, 0, tzinfo=_maintenance_timezone)
MAINTENANCE_END = datetime(2026, 9, 3, 18, 0, tzinfo=_maintenance_timezone)

# Secret nur aus Env (z.B. in WSGI gesetzt)
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']

# 🔹 PRODUKTIONS-DATENBANK AUF PYTHONANYWHERE
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'brainboost_online'),
        'USER': os.environ.get('POSTGRES_USER', 'admin'),
        'PASSWORD': os.environ['POSTGRES_PASSWORD'],
        'HOST': os.environ.get(
            'POSTGRES_HOST',
            'brainboost-4941.postgres.pythonanywhere-services.com',
        ),
        'PORT': os.environ.get('POSTGRES_PORT', '14941'),
    }
}

# Hosts / CSRF
_hosts = os.getenv(
    "DJANGO_ALLOWED_HOSTS",
    "brainboost.pythonanywhere.com,www.nachhilfe-brainboost.de,nachhilfe-brainboost.de"
)
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()]

CSRF_TRUSTED_ORIGINS = [
    f"https://{h}" for h in ALLOWED_HOSTS
    if h not in ("localhost", "127.0.0.1")
]


# Static / Media (PA)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'media'

# Security für HTTPS auf PA
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# MAILERS and sender/recipient settings are inherited from base and use the
# production environment loaded before these settings are imported.
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://www.nachhilfe-brainboost.de")
STRIPE_PUBLIC_KEY = os.environ["STRIPE_PUBLIC_KEY"]
STRIPE_SECRET_KEY = os.environ["STRIPE_SECRET_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
