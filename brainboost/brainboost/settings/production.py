from .base import *
import os

# Production: kein Debug
DEBUG = False

# Secret nur aus Env (z.B. in WSGI gesetzt)
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']

# 🔹 PRODUKTIONS-DATENBANK AUF PYTHONANYWHERE
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'super$default'),
        'USER': os.environ.get('POSTGRES_USER', 'super'),
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
    'DJANGO_ALLOWED_HOSTS',
    'localhost,127.0.0.1,brainboost.pythonanywhere.com',
)
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(',') if h.strip()]

CSRF_TRUSTED_ORIGINS = [
    f"https://{h}"
    for h in ALLOWED_HOSTS
    if h not in ('localhost', '127.0.0.1')
]

# Static / Media (PA)
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'media'

# Security für HTTPS auf PA
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True


