from django.conf import settings


def google_maps(request):
    return {
        "google_maps_api_key": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
    }


def meta_tracking(request):
    return {
        "cookiebot_id": getattr(settings, "COOKIEBOT_ID", ""),
        "meta_pixel_id": getattr(settings, "META_PIXEL_ID", ""),
    }


def contact_settings(request):
    return {
        "public_contact_email": getattr(
            settings,
            "PUBLIC_CONTACT_EMAIL",
            "kontakt@nachhilfe-brainboost.de",
        ),
    }
