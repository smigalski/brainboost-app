"""Local settings for manually testing the agreement and Stripe workflow."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *


EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
APP_BASE_URL = "http://localhost:8000"

STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY", "").strip()
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

if STRIPE_PUBLIC_KEY and not STRIPE_PUBLIC_KEY.startswith("pk_test_"):
    raise ImproperlyConfigured(
        "local_agreement_test akzeptiert nur Stripe-Testschlüssel (pk_test_...)."
    )
if STRIPE_SECRET_KEY and not STRIPE_SECRET_KEY.startswith("sk_test_"):
    raise ImproperlyConfigured(
        "local_agreement_test akzeptiert nur Stripe-Testschlüssel (sk_test_...)."
    )
if STRIPE_WEBHOOK_SECRET and not STRIPE_WEBHOOK_SECRET.startswith("whsec_"):
    raise ImproperlyConfigured(
        "STRIPE_WEBHOOK_SECRET muss mit whsec_ beginnen."
    )
