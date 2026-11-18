"""Alert -> context -> signals -> local LLM -> guarded proposal -> notify + audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

from .actions import GitOpsActor
from .audit import AuditLog
from .collectors import Collector, Window
from .collectors.base import workload_from_pod
from .config import Settings
from .llm import LLMClient, build_messages
from .models import Alert, Category, Incident, IncidentContext, RCAResult, Remediation, Signal, Target, utcnow
from .notify import Notifier
from .preprocess import detect_signals, deterministic_rca, render_context, summarize_logs
from .runbooks import Runbook, RunbookStore

log = logging.getLogger("aiops.pipeline")

# A rule-based finding at or above this confidence is treated as a fact the LLM must agree with.
STRONG_RULE = 0.8


def target_from_alert(alert: Alert) -> Target:
    lb = alert.labels
    pod = lb.get("pod")
    workload = lb.get("deployment") or lb.get("workload") or workload_from_pod(pod)
    return Target(namespace=lb.get("namespace"), pod=pod, container=lb.get("container"), workload=workload)


def incident_id(alert: Alert) -> str:
    key = alert.fingerprint or json.dumps(alert.labels, sort_keys=True)
    return f"{utcnow():%Y%m%d-%H%M%S}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def merge_results(det: RCAResult, llm: dict | None, candidates: list[Runbook]) -> RCAResult:
    """Combine the model's answer with the rule-based one. Rules win on hard facts."""
    if llm is None:
        return det
    try:
        category = Category(str(llm.get("category", "unknown")).strip().lower())
    except ValueError:
        category = Category.UNKNOWN
    try:
        confidence = max(0.0, min(1.0, float(llm.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    runbook_id = llm.get("runbook_id")
    if runbook_id not in {rb.id for rb in candidates}:
        runbook_id = None
    evidence = [str(e)[:300] for e in (llm.get("evidence") or []) if e][:4]
    root_cause = str(llm.get("root_cause") or "").strip()[:800]

    if det.category != Category.UNKNOWN and det.confidence >= STRONG_RULE and category != det.category:
        return det.model_copy(update={
            "confidence": min(det.confidence, 0.5),
            "notes": det.notes + [f"LLM '{category.value}' dedi, kural tabanlı analiz '{det.category.value}' "
                                  "buldu; güven düşürüldü, otomatik PR açılmaz."],
        })
    if category == det.category and category != Category.UNKNOWN:
        return RCAResult(category=category, root_cause=root_cause or det.root_cause,
                         confidence=round(min(0.95, max(confidence, det.confidence)), 2),
                         evidence=evidence or det.evidence, runbook_id=runbook_id, source="llm+deterministic")
    # No strong rule: trust the model, but cap confidence without hard evidence.
    return RCAResult(category=category, root_cause=root_cause or det.root_cause,
                     confidence=round(min(confidence, 0.7), 2), evidence=evidence or det.evidence,
                     runbook_id=runbook_id, source="llm")


def build_remediation(result: RCAResult, runbook: Runbook | None, signals: list[Signal]) -> Remediation:
    if runbook is None:
        return Remediation(kind="none", description="Eşleşen runbook yok; manuel inceleme önerilir.")
    description = runbook.title + (": " + " ".join(runbook.steps) if runbook.steps else "")
    rem = runbook.remediation
    if rem.kind != "gitops_patch":
        return Remediation(kind="manual", description=description)
    params = dict(rem.params)
    by_kind = {s.kind: s for s in signals}
    for s in signals:
        if "container" in s.data and "container" not in params:
            params["container"] = s.data["container"]
    if rem.action == "rollback_image":
        prev = by_kind.get("recent_rollout")
        if not prev or not prev.data.get("previous_image"):
            return Remediation(kind="manual", description=description + " (Önceki imaj bilinmiyor.)")
        params["image"] = prev.data["previous_image"]
    return Remediation(kind="gitops_patch", action=rem.action, params=params, description=description)


class Pipeline:
    def __init__(self, settings: Settings, collectors: list[Collector], runbooks: RunbookStore,
                 llm: LLMClient | None = None, actor: GitOpsActor | None = None,
                 notifier: Notifier | None = None, audit: AuditLog | None = None):
        self.s = settings
        self.collectors = collectors
        self.runbooks = runbooks
        self.llm = llm
        self.actor = actor
        self.notifier = notifier
        self.audit = audit

    def _audit(self, event: str, incident: Incident, **data) -> None:
        if self.audit:
            self.audit.append(event, incident.id, **data)

    async def _collect(self, alert: Alert, target: Target, window: Window) -> IncidentContext:
        ctx = IncidentContext()

        async def run(c: Collector) -> None:
            try:
                await asyncio.wait_for(c.collect(alert, target, window, ctx), timeout=30)
            except Exception as exc:  # noqa: BLE001
                ctx.errors.append(f"{c.name}: {exc}")

        # Kubernetes first: it resolves the workload the other collectors filter on.
        first = [c for c in self.collectors if c.name in ("kubernetes", "fixture")]
        rest = [c for c in self.collectors if c not in first]
        for c in first:
            await run(c)
        await asyncio.gather(*(run(c) for c in rest))
        return ctx

    async def run(self, alert: Alert) -> Incident:
        t0 = time.perf_counter()
        target = target_from_alert(alert)
        incident = Incident(id=incident_id(alert), alert=alert, target=target)
        self._audit("alert_received", incident, alert=alert.name, labels=alert.labels)

        reference = alert.startsAt or utcnow()
        window = Window(start=reference - timedelta(minutes=self.s.context.window_minutes), end=utcnow())
        incident.context = await self._collect(alert, target, window)
        incident.context.log_templates = summarize_logs(incident.context.log_lines)
        incident.signals = detect_signals(incident.context, reference, self.s.context.window_minutes)
        incident.timings_ms["collect"] = int((time.perf_counter() - t0) * 1000)

        det = deterministic_rca(incident.signals)
        candidates = self.runbooks.match(incident.signals, alert)
        llm_answer = None
        if self.llm is not None:
            t1 = time.perf_counter()
            context_text = render_context(alert, target, incident.context, incident.signals,
                                          self.s.context.budget_chars)
            try:
                llm_answer, usage = await self.llm.complete_json(
                    build_messages(context_text, candidates, self.s.llm.language))
                incident.timings_ms["llm"] = int((time.perf_counter() - t1) * 1000)
                self._audit("llm_called", incident, model=self.llm.model, usage=usage,
                            ms=incident.timings_ms["llm"])
            except Exception as exc:  # noqa: BLE001 - fall back to rules
                det.notes.append(f"LLM kullanılamadı, kural tabanlı sonuç kullanıldı: {exc}")
                self._audit("llm_failed", incident, error=str(exc)[:300])

        result = merge_results(det, llm_answer, candidates)
        runbook = self.runbooks.get(result.runbook_id)
        if runbook is None or runbook.category != result.category:
            runbook = self.runbooks.for_category(result.category)
        result.runbook_id = runbook.id if runbook else None
        result.remediation = build_remediation(result, runbook, incident.signals)
        incident.analysis = result
        self._audit("analysis_done", incident, category=result.category.value, confidence=result.confidence,
                    source=result.source, runbook=result.runbook_id)

        await self._act(incident)
        incident.timings_ms["total"] = int((time.perf_counter() - t0) * 1000)
        self._save(incident)
        return incident

    async def _act(self, incident: Incident) -> None:
        a = incident.analysis
        policy = self.s.policy
        if self.actor and a.remediation.kind == "gitops_patch":
            allowed = policy.autonomy_level >= 1 and a.confidence >= policy.min_confidence
            if not allowed:
                a.notes.append(f"PR açılmadı (seviye {policy.autonomy_level}, güven {a.confidence:.0%} < "
                               f"{policy.min_confidence:.0%} veya seviye 0); sadece diff önizlemesi üretildi.")
            record = await self.actor.propose(incident, preview_only=not allowed)
            incident.actions.append(record)
            self._audit("action", incident, kind=record.kind, status=record.status, url=record.url,
                        detail=record.detail)
        if self.notifier:
            record = await self.notifier.send(incident)
            incident.actions.append(record)
            self._audit("notify", incident, status=record.status)

    def _save(self, incident: Incident) -> None:
        out = Path(self.s.output_dir) / "incidents"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{incident.id}.json").write_text(incident.model_dump_json(indent=2))
