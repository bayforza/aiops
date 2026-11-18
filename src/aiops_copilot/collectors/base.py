from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..models import Alert, IncidentContext, Target


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


class Collector(Protocol):
    name: str

    async def collect(self, alert: Alert, target: Target, window: Window, ctx: IncidentContext) -> None:
        """Add what this source knows to ``ctx``. Must never modify the source system."""


_POD_HASH = re.compile(r"^(?P<base>.+?)-[a-z0-9]{8,10}-[a-z0-9]{5}$")


def workload_from_pod(pod: str | None) -> str | None:
    """Best-effort Deployment name from a pod name like ``api-7d9f8b6c5d-x2k4q``."""
    if not pod:
        return None
    m = _POD_HASH.match(pod)
    return m.group("base") if m else None


def pod_selector(target: Target) -> str:
    """A PromQL/LogQL regex that matches all pods of the workload (pods get replaced)."""
    if target.workload:
        return f"{re.escape(target.workload)}-.*"
    return re.escape(target.pod or ".*")
