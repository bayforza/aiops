from __future__ import annotations

from aiops_copilot.confidence import assess, signal_source
from aiops_copilot.models import Category, RCAResult, Signal

OOM = Signal(kind="oom_killed", detail="container app OOMKilled", weight=1.0)
MEM = Signal(kind="memory_near_limit", detail="memory 98% of limit", weight=0.6)
LOG = Signal(kind="crash_loop", detail="log: java.lang.OutOfMemoryError", weight=0.5)


def test_signal_sources():
    assert signal_source(OOM) == "k8s"
    assert signal_source(MEM) == "metrics"
    assert signal_source(LOG) == "logs"


def test_independent_sources_and_agreement_raise_score():
    rules = RCAResult(category=Category.OOM)
    one = assess(Category.OOM, [OOM], rules)
    three = assess(Category.OOM, [OOM, MEM, LOG], rules, llm_category=Category.OOM)
    assert one.score == 0.7  # 0.5 * 1.0 + rules agree
    assert three.score == 0.95  # capped
    assert three.sources == ["k8s", "logs", "metrics"]


def test_no_supporting_signal_is_weak():
    ev = assess(Category.DISK_FULL, [OOM], RCAResult(category=Category.OOM), llm_category=Category.DISK_FULL)
    assert ev.score < 0.5
    assert any("no deterministic signal" in r for r in ev.reasons)
    assert any("rule-based analysis differs" in r for r in ev.reasons)


def test_model_disagreement_lowers_score():
    rules = RCAResult(category=Category.OOM)
    agree = assess(Category.OOM, [OOM], rules, llm_category=Category.OOM)
    disagree = assess(Category.OOM, [OOM], rules, llm_category=Category.BAD_DEPLOY)
    assert disagree.score < agree.score


def test_unknown_and_history_terms():
    assert assess(Category.UNKNOWN, [], RCAResult()).score == 0.2
    rules = RCAResult(category=Category.OOM)
    base = assess(Category.OOM, [OOM], rules).score
    assert assess(Category.OOM, [OOM], rules, past_success_rate=1.0, similar_incidents=2).score > base
    assert assess(Category.OOM, [OOM], rules, past_success_rate=0.0).score < base
