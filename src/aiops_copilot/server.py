"""HTTP API: Alertmanager webhook receiver and incident listing."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .models import Alert, Incident
from .pipeline import Pipeline

log = logging.getLogger("aiops.server")


class AlertmanagerPayload(BaseModel):
    status: str = "firing"
    alerts: list[Alert] = Field(default_factory=list)
    commonLabels: dict[str, str] = Field(default_factory=dict)


def create_app(pipeline: Pipeline, dedupe_seconds: int = 900, max_concurrent: int = 1) -> FastAPI:
    app = FastAPI(title="AIOps Copilot", version="0.1.0")
    incidents: dict[str, Incident] = {}
    recent: dict[str, float] = {}
    # One analysis at a time by default: a CPU-hosted model serves requests sequentially anyway.
    sem = asyncio.Semaphore(max_concurrent)
    tasks: set[asyncio.Task] = set()

    async def process(alert: Alert) -> None:
        async with sem:
            try:
                inc = await pipeline.run(alert)
                incidents[inc.id] = inc
            except Exception:  # noqa: BLE001
                log.exception("analysis failed for %s", alert.name)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/alertmanager", status_code=202)
    async def webhook(payload: AlertmanagerPayload) -> dict[str, Any]:
        now = time.time()
        accepted, skipped = [], []
        for alert in payload.alerts:
            if alert.status != "firing":
                skipped.append(alert.name)
                continue
            key = alert.fingerprint or str(sorted(alert.labels.items()))
            if now - recent.get(key, 0) < dedupe_seconds:
                skipped.append(alert.name)
                continue
            recent[key] = now
            task = asyncio.create_task(process(alert))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            accepted.append(alert.name)
        return {"accepted": accepted, "skipped": skipped}

    @app.get("/incidents")
    async def list_incidents() -> list[dict[str, Any]]:
        return [
            {"id": i.id, "alert": i.alert.name, "target": i.target.model_dump(),
             "category": i.analysis.category.value, "confidence": i.analysis.confidence,
             "actions": [a.model_dump(exclude={"diff"}) for a in i.actions]}
            for i in sorted(incidents.values(), key=lambda x: x.created_at, reverse=True)
        ]

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> Incident:
        if incident_id not in incidents:
            raise HTTPException(404, "incident not found")
        return incidents[incident_id]

    return app
