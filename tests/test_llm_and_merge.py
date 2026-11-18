from __future__ import annotations

import pytest

from aiops_copilot.llm import build_messages, parse_json_object
from aiops_copilot.models import Category, RCAResult
from aiops_copilot.pipeline import merge_results


@pytest.mark.parametrize("text", [
    '{"category": "oom", "confidence": 0.8}',
    '<think>let me see {not json}</think>\n{"category": "oom", "confidence": 0.8}',
    '```json\n{"category": "oom", "confidence": 0.8}\n```',
    'Sure! Here it is: {"category": "oom", "confidence": 0.8} hope it helps',
])
def test_parse_json_object(text):
    assert parse_json_object(text)["category"] == "oom"


def test_parse_json_object_fails():
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_prompt_lists_only_candidates(runbooks):
    rb = [runbooks.get("oom-increase-memory")]
    msgs = build_messages("ctx", rb, "tr")
    assert "Turkish" in msgs[0]["content"]
    assert "oom-increase-memory" in msgs[1]["content"]
    assert "bad-deploy-rollback" not in msgs[1]["content"]


def _det(cat: Category, conf: float) -> RCAResult:
    return RCAResult(category=cat, confidence=conf, root_cause="rule", evidence=["e"])


def test_strong_rule_overrides_disagreeing_llm(runbooks):
    out = merge_results(_det(Category.OOM, 0.9), {"category": "dependency", "confidence": 0.9}, [])
    assert out.category == Category.OOM
    assert out.confidence <= 0.5
    assert out.notes


def test_agreement_raises_confidence(runbooks):
    rb = [runbooks.get("bad-deploy-rollback")]
    out = merge_results(_det(Category.BAD_DEPLOY, 0.54),
                        {"category": "bad_deploy", "confidence": 0.85, "root_cause": "yeni imaj",
                         "runbook_id": "bad-deploy-rollback"}, rb)
    assert out.source == "llm+deterministic"
    assert out.confidence == 0.85 and out.runbook_id == "bad-deploy-rollback"


def test_llm_alone_is_capped_and_runbook_must_be_candidate():
    out = merge_results(_det(Category.UNKNOWN, 0.2),
                        {"category": "config_error", "confidence": 0.99, "runbook_id": "made-up"}, [])
    assert out.category == Category.CONFIG_ERROR
    assert out.confidence == 0.7
    assert out.runbook_id is None


def test_invalid_llm_category_becomes_unknown():
    out = merge_results(_det(Category.UNKNOWN, 0.2), {"category": "aliens", "confidence": "high"}, [])
    assert out.category == Category.UNKNOWN and out.confidence == 0.0
