"""Replays recorded incident context. Used by the eval harness and tests."""

from __future__ import annotations

from ..models import Alert, IncidentContext, Target
from .base import Window


class FixtureCollector:
    name = "fixture"

    def __init__(self, context: IncidentContext, workload: str | None = None):
        self.context = context
        self.workload = workload

    async def collect(self, alert: Alert, target: Target, window: Window, ctx: IncidentContext) -> None:
        if self.workload and not target.workload:
            target.workload = self.workload
        src = self.context
        ctx.containers.extend(src.containers)
        ctx.events.extend(src.events)
        ctx.log_lines.extend(src.log_lines)
        ctx.metrics.extend(src.metrics)
        ctx.changes.extend(src.changes)
