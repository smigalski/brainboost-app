UTM_SESSION_KEY = "lead_attribution"
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


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
