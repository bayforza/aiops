from __future__ import annotations

import httpx

from ..models import Alert, IncidentContext, Target
from .base import Window, pod_selector


class LokiCollector:
    name = "loki"

    def __init__(self, url: str, max_lines: int = 500, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.max_lines = max_lines
        self.client = client or httpx.AsyncClient(timeout=15)

    async def collect(self, alert: Alert, target: Target, window: Window, ctx: IncidentContext) -> None:
        if not target.namespace:
            return
        query = f'{{namespace="{target.namespace}", pod=~"{pod_selector(target)}"}}'
        try:
            resp = await self.client.get(
                f"{self.url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": int(window.start.timestamp() * 1e9),
                    "end": int(window.end.timestamp() * 1e9),
                    "limit": self.max_lines,
                    "direction": "backward",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            ctx.errors.append(f"loki: {exc}")
            return
        entries: list[tuple[int, str]] = []
        for stream in resp.json().get("data", {}).get("result", []):
            for ts, line in stream.get("values", []):
                entries.append((int(ts), line))
        entries.sort()
        ctx.log_lines.extend(line for _, line in entries)
