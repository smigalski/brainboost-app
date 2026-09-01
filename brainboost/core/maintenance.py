from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone


@dataclass(frozen=True)
class MaintenanceStatus:
    enabled: bool
    announced: bool
    active: bool
    start: Optional[datetime]
    end: Optional[datetime]


def get_maintenance_status(*, now=None) -> MaintenanceStatus:
    enabled = bool(getattr(settings, "MAINTENANCE_MODE_ENABLED", False))
    start = getattr(settings, "MAINTENANCE_START", None)
    end = getattr(settings, "MAINTENANCE_END", None)
    current_time = now or timezone.now()

    configured = enabled and start is not None and end is not None and start < end
    if not configured:
        return MaintenanceStatus(False, False, False, start, end)

    return MaintenanceStatus(
        enabled=True,
        announced=current_time < end,
        active=start <= current_time < end,
        start=start,
        end=end,
    )


def maintenance_view(request):
    status = get_maintenance_status()
    return render(
        request,
        "maintenance.html",
        {"maintenance": status},
        status=503 if status.active else 200,
    )
