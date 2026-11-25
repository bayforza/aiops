"""Local / cloud routing: when may an incident summary get a second opinion from a cloud model?

Default: never. Cloud fallback is off unless the operator enables it, and even then:

- incidents in local-only namespaces, or labelled as restricted, stay local;
- only an anonymised summary is sent (see anonymize.py), never raw logs;
- the local answer is kept when the evidence already supports it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .confidence import EvidenceAssessment
from .config import CloudConfig
from .models import Alert, Category, RCAResult, Target

LOCAL = "local"
LOCAL_ONLY = "local_only"
CLOUD = "cloud"


@dataclass
class RouteDecision:
    route: str
    reason: str


def decide(cfg: CloudConfig, result: RCAResult, evidence: EvidenceAssessment, alert: Alert,
           target: Target) -> RouteDecision:
    if not cfg.enabled:
        return RouteDecision(LOCAL, "cloud fallback kapalı")
    if target.namespace and target.namespace in cfg.local_only_namespaces:
        return RouteDecision(LOCAL_ONLY, f"namespace {target.namespace} sadece yerel")
    label = alert.labels.get(cfg.classification_label, "").lower()
    if label and label in {v.lower() for v in cfg.local_only_classifications}:
        return RouteDecision(LOCAL_ONLY, f"{cfg.classification_label}={label}: veri sınıfı dışarı çıkamaz")
    if not os.environ.get(cfg.api_key_env):
        return RouteDecision(LOCAL, f"{cfg.api_key_env} tanımlı değil")
    score = min(result.confidence, evidence.score)
    if result.category != Category.UNKNOWN and score >= cfg.min_local_confidence:
        return RouteDecision(LOCAL, f"yerel sonuç yeterli (kanıt skoru {score:.2f})")
    return RouteDecision(CLOUD, f"yerel sonuç belirsiz (kanıt skoru {score:.2f} < {cfg.min_local_confidence:.2f})")
