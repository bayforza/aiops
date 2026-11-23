from __future__ import annotations

import httpx

from ..models import Alert, IncidentContext, MetricSummary, Target
from .base import Window, pod_selector

# name -> (query template, unit). {ns} and {pods} are filled per incident.
QUERIES: dict[str, tuple[str, str]] = {
    "memory_usage_vs_limit": (
        'max(container_memory_working_set_bytes{{namespace="{ns}",pod=~"{pods}",container!=""}})'
        ' / max(kube_pod_container_resource_limits{{namespace="{ns}",pod=~"{pods}",resource="memory"}})',
        "ratio",
    ),
    "container_restarts": (
        'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod=~"{pods}"}}[10m]))',
        "count/10m",
    ),
    "cpu_throttling": (
        'sum(rate(container_cpu_cfs_throttled_periods_total{{namespace="{ns}",pod=~"{pods}"}}[5m]))'
        ' / sum(rate(container_cpu_cfs_periods_total{{namespace="{ns}",pod=~"{pods}"}}[5m]))',
        "ratio",
    ),
    "node_disk_used": (
        "max(1 - node_filesystem_avail_bytes{{fstype!~\"tmpfs|overlay\"}}"
        " / node_filesystem_size_bytes{{fstype!~\"tmpfs|overlay\"}})",
        "ratio",
    ),
}


class PrometheusCollector:
    name = "prometheus"

    def __init__(self, url: str, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=15)

    async def _range(self, query: str, window: Window) -> list[float]:
        resp = await self.client.get(
            f"{self.url}/api/v1/query_range",
            params={
                "query": query,
                "start": window.start.timestamp(),
                "end": window.end.timestamp(),
                "step": "60",
            },
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
        values: list[float] = []
        for series in result:
            for _, v in series.get("values", []):
                try:
                    f = float(v)
                except ValueError:
                    continue
                if f == f:  # drop NaN
                    values.append(f)
        return values

    async def collect(self, alert: Alert, target: Target, window: Window, ctx: IncidentContext) -> None:
        if not target.namespace:
            return
        pods = pod_selector(target)
        for name, (tmpl, unit) in QUERIES.items():
            query = tmpl.format(ns=target.namespace, pods=pods)
            try:
                values = await self._range(query, window)
            except (httpx.HTTPError, ValueError) as exc:
                ctx.errors.append(f"prometheus:{name}: {exc}")
                continue
            if values:
                ctx.metrics.append(
                    MetricSummary(
                        name=name, query=query, last=values[-1], max=max(values), min=min(values), unit=unit
                    )
                )
