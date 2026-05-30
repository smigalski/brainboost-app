import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.sitemaps import get_canonical_host, get_static_sitemap_urls


logger = logging.getLogger(__name__)


def _mask_key(key):
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


class Command(BaseCommand):
    help = "Submit public BrainBoost URLs to IndexNow."

    endpoint = "https://api.indexnow.org/IndexNow"

    def handle(self, *args, **options):
        if settings.DEBUG:
            raise CommandError("IndexNow ist deaktiviert, solange DEBUG=True ist.")

        key = settings.INDEXNOW_KEY
        if not key:
            raise CommandError("INDEXNOW_KEY fehlt. IndexNow-Submission abgebrochen.")

        host = get_canonical_host()
        payload = {
            "host": host,
            "key": key,
            "keyLocation": f"https://{host}/{key}.txt",
            "urlList": get_static_sitemap_urls(),
        }
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=15) as response:
                status = response.getcode()
        except HTTPError as exc:
            message = self._error_message(exc.code)
            logger.error("IndexNow Fehler %s: %s", exc.code, message)
            raise CommandError(f"IndexNow Fehler {exc.code}: {message}") from exc
        except URLError as exc:
            logger.error("IndexNow Netzwerkfehler: %s", exc.reason)
            raise CommandError(f"IndexNow Netzwerkfehler: {exc.reason}") from exc

        if status in (200, 202):
            logger.info("IndexNow erfolgreich mit Status %s.", status)
            self.stdout.write(
                self.style.SUCCESS(
                    f"IndexNow erfolgreich: Status {status}, {len(payload['urlList'])} URLs, Key {_mask_key(key)}"
                )
            )
            return

        message = self._error_message(status)
        logger.error("IndexNow unerwarteter Status %s: %s", status, message)
        raise CommandError(f"IndexNow unerwarteter Status {status}: {message}")

    def _error_message(self, status):
        messages = {
            400: "Ungültige Anfrage. Prüfe Payload, Host und URL-Liste.",
            403: "Nicht autorisiert. Prüfe INDEXNOW_KEY und Key-Datei.",
            422: "URLs gehören nicht zum angegebenen Host oder sind ungültig.",
            429: "Zu viele Anfragen. Submission später erneut versuchen.",
        }
        return messages.get(status, "IndexNow hat die Submission nicht akzeptiert.")
