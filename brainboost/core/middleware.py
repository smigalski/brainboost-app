UTM_SESSION_KEY = "lead_attribution"
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


class MaintenanceModeMiddleware:
    PUBLIC_URL_NAMES = {
        "agbs",
        "contact",
        "impressum",
        "landing_eltern",
        "landing_page",
        "landing_schuelerinnen",
        "lead_thanks_tutor",
        "lead_thanks_tutoring",
        "login",
        "logout",
        "maintenance",
        "nachhilfe_anfrage",
        "pricing",
        "stripe_webhook",
        "tutor_werden",
        "tutorin_werden",
        "robots_txt",
        "django.contrib.sitemaps.views.sitemap",
        "set_language",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.contrib.auth import logout
        from django.shortcuts import redirect
        from django.urls import resolve, Resolver404

        from .maintenance import get_maintenance_status

        status = get_maintenance_status()
        url_name = None
        if status.active and not self._has_staff_access(request):
            try:
                url_name = resolve(request.path_info).url_name
            except Resolver404:
                url_name = None
            if not self._is_public_request(request.path_info, url_name):
                return redirect("maintenance")
        response = self.get_response(request)
        # The login URL must remain reachable for staff. If a normal account
        # successfully authenticates there, remove the new session immediately.
        if (
            status.active
            and url_name == "login"
            and request.user.is_authenticated
            and not self._has_staff_access(request)
        ):
            logout(request)
            return redirect("maintenance")
        return response

    @staticmethod
    def _has_staff_access(request):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))

    def _is_public_request(self, path, url_name):
        return (
            url_name in self.PUBLIC_URL_NAMES
            or path.startswith("/admin/")
            or path.startswith("/static/")
            or path.startswith("/media/")
        )


class UTMTrackingMiddleware:
    """Keep first-touch attribution in session until a lead form is submitted."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET" and hasattr(request, "session"):
            self._store_attribution(request)
        return self.get_response(request)

    def _store_attribution(self, request):
        attribution = dict(request.session.get(UTM_SESSION_KEY, {}))
        changed = False

        if not attribution.get("landing_page_path"):
            attribution["landing_page_path"] = request.path
            changed = True

        if not attribution.get("initial_querystring") and request.META.get("QUERY_STRING"):
            attribution["initial_querystring"] = request.META.get("QUERY_STRING", "")
            changed = True

        if not attribution.get("referrer") and request.META.get("HTTP_REFERER"):
            attribution["referrer"] = request.META.get("HTTP_REFERER", "")
            changed = True

        for key in UTM_KEYS:
            value = request.GET.get(key, "").strip()
            if value and not attribution.get(key):
                attribution[key] = value
                changed = True

        if request.GET.get("campaign", "").strip() and not attribution.get("campaign"):
            attribution["campaign"] = request.GET.get("campaign", "").strip()
            changed = True
        elif attribution.get("utm_campaign") and not attribution.get("campaign"):
            attribution["campaign"] = attribution["utm_campaign"]
            changed = True

        if request.GET.get("source", "").strip() and not attribution.get("source"):
            attribution["source"] = request.GET.get("source", "").strip()
            changed = True

        if changed:
            request.session[UTM_SESSION_KEY] = attribution
            request.session.modified = True
