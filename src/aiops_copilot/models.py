"""Core data models shared across the pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Category(StrEnum):
    OOM = "oom"
    IMAGE_PULL = "image_pull"
    CONFIG_ERROR = "config_error"
    BAD_DEPLOY = "bad_deploy"
    PROBE_FAILURE = "probe_failure"
    DISK_FULL = "disk_full"
    DEPENDENCY = "dependency"
    UNKNOWN = "unknown"


class Alert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime | None = None
    fingerprint: str | None = None

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "unknown")


class Target(BaseModel):
    namespace: str | None = None
    pod: str | None = None
    container: str | None = None
    workload: str | None = None  # Deployment name


class ContainerState(BaseModel):
    name: str
    image: str | None = None
    ready: bool = True
    restart_count: int = 0
    waiting_reason: str | None = None
    waiting_message: str | None = None
    last_terminated_reason: str | None = None
    last_exit_code: int | None = None
    memory_limit: str | None = None


class MetricSummary(BaseModel):
    name: str
    query: str
    last: float | None = None
    max: float | None = None
    min: float | None = None
    unit: str = ""


class LogTemplate(BaseModel):
    template: str
    count: int
    sample: str
    is_error: bool = False


class Change(BaseModel):
    at: datetime | None = None
    source: str  # "k8s-rollout" | "git"
    description: str
    image: str | None = None
    revision: str | None = None


class IncidentContext(BaseModel):
    containers: list[ContainerState] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    log_lines: list[str] = Field(default_factory=list)
    log_templates: list[LogTemplate] = Field(default_factory=list)
    metrics: list[MetricSummary] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)  # collector failures, kept for transparency


class Signal(BaseModel):
    kind: str
    detail: str
    weight: float = 0.5  # 1.0 = unambiguous evidence
    data: dict[str, Any] = Field(default_factory=dict)


class Remediation(BaseModel):
    kind: str = "none"  # gitops_patch | manual | none
    description: str = ""
    action: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class RCAResult(BaseModel):
    category: Category = Category.UNKNOWN
    root_cause: str = ""
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    runbook_id: str | None = None
    remediation: Remediation = Field(default_factory=Remediation)
    source: str = "deterministic"  # deterministic | llm | llm+deterministic | cloud | cloud+deterministic
    notes: list[str] = Field(default_factory=list)
    # Evidence-based confidence (confidence.py), independent of what the model says about itself.
    evidence_score: float | None = None
    confidence_reasons: list[str] = Field(default_factory=list)
    # Local / cloud routing (routing.py).
    route: str = "local"  # local | local_only | cloud
    route_reason: str = ""
    second_opinion: dict[str, Any] | None = None


class ActionRecord(BaseModel):
    kind: str  # notify | pull_request | dry_run_patch | skipped
    status: str
    detail: str = ""
    url: str | None = None
    diff: str | None = None


class Incident(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    alert: Alert
    target: Target
    context: IncidentContext = Field(default_factory=IncidentContext)
    signals: list[Signal] = Field(default_factory=list)
    analysis: RCAResult = Field(default_factory=RCAResult)
    actions: list[ActionRecord] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
