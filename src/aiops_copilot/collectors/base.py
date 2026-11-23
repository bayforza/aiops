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


_REGEX_META = set(".^$*+?()[]{}|\\")


def _quoted_regex_literal(name: str) -> str:
    """Escape ``name`` for a regex placed inside a double-quoted PromQL/LogQL string.

    ``re.escape`` is not usable here: it also escapes ``-`` and the resulting ``\\-``
    is an invalid string escape in PromQL/LogQL (HTTP 400). Only real regex
    metacharacters are escaped, and the backslash is doubled for the string literal.
    """
    return "".join("\\\\" + c if c in _REGEX_META else c for c in name)


def pod_selector(target: Target) -> str:
    """A PromQL/LogQL regex (as the inside of a double-quoted string) that matches
    all pods of the workload (pods get replaced)."""
    if target.workload:
        return f"{_quoted_regex_literal(target.workload)}-.*"
    if target.pod:
        return _quoted_regex_literal(target.pod)
    return ".*"
