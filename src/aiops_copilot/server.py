"""HTTP API: Alertmanager webhook receiver, incident API for the UI, feedback and statistics."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .models import Alert, Incident, utcnow
from .pipeline import Pipeline
from .stats import compute
from .store import Store

log = logging.getLogger("aiops.server")
UI_DIR = Path(__file__).parent / "ui"

Vote = Literal["correct", "partial", "wrong"]


class AlertmanagerPayload(BaseModel):
    status: str = "firing"
    alerts: list[Alert] = Field(default_factory=list)
    commonLabels: dict[str, str] = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    root_cause: Vote | None = None
    fix: Vote | None = None
    comment: str = Field("", max_length=2000)
    author: str | None = Field(None, max_length=100)


class StatusIn(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]


def summary(inc: Incident, vote: dict[str, Any] | None) -> dict[str, Any]:
    a = inc.analysis
    pr = next((x.url for x in inc.actions if x.url), None)
    return {
        "id": inc.id, "created_at": inc.created_at, "status": inc.status, "alert": inc.alert.name,
        "severity": inc.alert.labels.get("severity"), "target": inc.target.model_dump(),
        "category": a.category.value, "root_cause": a.root_cause, "confidence": a.confidence,
        "evidence_score": a.evidence_score, "source": a.source, "route": a.route, "answered_by": a.answered_by,
        "runbook_id": a.runbook_id, "remediation_kind": a.remediation.kind, "pr_url": pr,
        "total_ms": inc.timings_ms.get("total"), "vote": vote,
        # Kept for clients of the first API version.
        "actions": [x.model_dump(exclude={"diff"}) for x in inc.actions],
    }


def create_app(pipeline: Pipeline, dedupe_seconds: int = 900, max_concurrent: int = 1,
               store: Store | None = None) -> FastAPI:
    app = FastAPI(title="AIOps Copilot", version=__version__)
    store = store or Store(":memory:")
    recent: dict[str, float] = {}
    # One analysis at a time by default: a CPU-hosted model serves requests sequentially anyway.
    sem = asyncio.Semaphore(max_concurrent)
    tasks: set[asyncio.Task] = set()

    def audit(event: str, incident_id: str, **data: Any) -> None:
        if pipeline.audit:
            pipeline.audit.append(event, incident_id, **data)

    async def process(alert: Alert, alert_id: int, received_at) -> None:
        async with sem:
            try:
                inc = await pipeline.run(alert, received_at=received_at)
                store.save_incident(inc)
                store.link_alert(alert_id, inc.id)
            except Exception:  # noqa: BLE001
                log.exception("analysis failed for %s", alert.name)
                store.link_alert(alert_id, None, "failed")

    def get_or_404(incident_id: str) -> Incident:
        inc = store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        return inc

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/alertmanager", status_code=202)
    async def webhook(payload: AlertmanagerPayload) -> dict[str, Any]:
        now = time.time()
        accepted, skipped = [], []
        for alert in payload.alerts:
            key = alert.fingerprint or str(sorted(alert.labels.items()))
            if alert.status != "firing":
                # Alertmanager says the alert cleared: close the incident it opened.
                store.record_alert(alert, "resolved")
                for row in store.alerts(2000):
                    if row["fingerprint"] == alert.fingerprint and row["incident_id"] and alert.fingerprint:
                        inc = store.set_status(row["incident_id"], "resolved")
                        if inc:
                            audit("status_changed", inc.id, status="resolved", by="alertmanager")
                        break
                skipped.append(alert.name)
                continue
            if now - recent.get(key, 0) < dedupe_seconds:
                store.record_alert(alert, "deduplicated")
                skipped.append(alert.name)
                continue
            recent[key] = now
            alert_id = store.record_alert(alert, "queued")
            task = asyncio.create_task(process(alert, alert_id, utcnow()))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            accepted.append(alert.name)
        return {"accepted": accepted, "skipped": skipped}

    # UI API ---------------------------------------------------------------------------------

    @app.get("/api/info")
    async def info() -> dict[str, Any]:
        s = pipeline.s
        cloud = pipeline.cloud_llm
        return {
            "version": __version__,
            "local_models": [{"name": c.name, "model": c.model, "provider": getattr(c, "provider", "openai")}
                             for c in pipeline.llms],
            "cloud": {"enabled": cloud is not None, "provider": s.cloud.provider,
                      "model": s.cloud.model if cloud is not None else None,
                      "min_local_confidence": s.cloud.min_local_confidence,
                      "local_only_namespaces": s.cloud.local_only_namespaces},
            "autonomy_level": s.policy.autonomy_level, "min_confidence": s.policy.min_confidence,
            "dry_run": s.gitops.dry_run, "queued": len(tasks),
        }

    @app.get("/api/incidents")
    async def api_incidents(status: str | None = None, category: str | None = None, q: str | None = None,
                            limit: int = 200) -> list[dict[str, Any]]:
        votes = store.latest_votes()
        out = []
        for inc in store.incidents(limit=max(1, min(limit, 1000))):
            if status and inc.status != status:
                continue
            if category and inc.analysis.category.value != category:
                continue
            if q:
                hay = " ".join([inc.id, inc.alert.name, inc.analysis.root_cause, *inc.alert.labels.values()]).lower()
                if q.lower() not in hay:
                    continue
            out.append(summary(inc, votes.get(inc.id)))
        return out

    @app.get("/api/incidents/{incident_id}")
    async def api_incident(incident_id: str) -> dict[str, Any]:
        inc = get_or_404(incident_id)
        runbook = pipeline.runbooks.get(inc.analysis.runbook_id)
        return {
            "incident": inc.model_dump(mode="json"),
            "runbook": runbook.model_dump(mode="json") if runbook else None,
            "feedback": store.feedback(incident_id),
            "timeline": pipeline.audit.events(incident_id) if pipeline.audit else [],
        }

    @app.post("/api/incidents/{incident_id}/feedback", status_code=201)
    async def api_feedback(incident_id: str, body: FeedbackIn) -> dict[str, Any]:
        inc = get_or_404(incident_id)
        if body.root_cause is None and body.fix is None and not body.comment:
            raise HTTPException(422, "give a root cause vote, a fix vote or a comment")
        fb = store.add_feedback(inc.id, body.root_cause, body.fix, body.comment, body.author)
        audit("feedback", inc.id, root_cause=body.root_cause, fix=body.fix, author=body.author,
              answered_by=inc.analysis.answered_by.get("name"))
        return fb

    @app.post("/api/incidents/{incident_id}/status")
    async def api_status(incident_id: str, body: StatusIn) -> dict[str, Any]:
        get_or_404(incident_id)
        inc = store.set_status(incident_id, body.status)
        audit("status_changed", incident_id, status=body.status, by="operator")
        return {"id": incident_id, "status": inc.status}

    @app.get("/api/alerts")
    async def api_alerts(limit: int = 300) -> list[dict[str, Any]]:
        return store.alerts(max(1, min(limit, 2000)))

    @app.get("/api/stats")
    async def api_stats() -> dict[str, Any]:
        return compute(store.incidents(limit=5000), store.latest_votes(), store.alerts(limit=20000))

    # First API version, kept for scripts.
    @app.get("/incidents")
    async def list_incidents() -> list[dict[str, Any]]:
        return [summary(i, None) for i in store.incidents()]

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> Incident:
        return get_or_404(incident_id)

    if UI_DIR.exists():
        app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse("/ui/")

    return app
