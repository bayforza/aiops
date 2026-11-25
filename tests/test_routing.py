from __future__ import annotations

import pytest

from aiops_copilot.confidence import EvidenceAssessment
from aiops_copilot.config import CloudConfig
from aiops_copilot.models import Alert, Category, RCAResult, Target
from aiops_copilot.routing import CLOUD, LOCAL, LOCAL_ONLY, decide

WEAK = RCAResult(category=Category.UNKNOWN, confidence=0.3)
STRONG = RCAResult(category=Category.OOM, confidence=0.9)
LOW_EV = EvidenceAssessment(0.2)
HIGH_EV = EvidenceAssessment(0.8)
ALERT = Alert(labels={"alertname": "X"})


@pytest.fixture
def cfg(monkeypatch) -> CloudConfig:
    monkeypatch.setenv("AIOPS_CLOUD_API_KEY", "test-key")
    return CloudConfig(enabled=True, local_only_namespaces=["core-banking"])


def test_disabled_by_default():
    assert decide(CloudConfig(), WEAK, LOW_EV, ALERT, Target()).route == LOCAL


def test_uncertain_goes_to_cloud(cfg):
    assert decide(cfg, WEAK, LOW_EV, ALERT, Target(namespace="shop")).route == CLOUD


def test_strong_local_result_stays_local(cfg):
    assert decide(cfg, STRONG, HIGH_EV, ALERT, Target(namespace="shop")).route == LOCAL


def test_high_model_confidence_without_evidence_still_goes_to_cloud(cfg):
    assert decide(cfg, STRONG, LOW_EV, ALERT, Target(namespace="shop")).route == CLOUD


def test_local_only_namespace(cfg):
    d = decide(cfg, WEAK, LOW_EV, ALERT, Target(namespace="core-banking"))
    assert d.route == LOCAL_ONLY and "core-banking" in d.reason


def test_classification_label(cfg):
    alert = Alert(labels={"alertname": "X", "data_classification": "Restricted"})
    assert decide(cfg, WEAK, LOW_EV, alert, Target(namespace="shop")).route == LOCAL_ONLY


def test_missing_api_key(cfg, monkeypatch):
    monkeypatch.delenv("AIOPS_CLOUD_API_KEY")
    assert decide(cfg, WEAK, LOW_EV, ALERT, Target(namespace="shop")).route == LOCAL
