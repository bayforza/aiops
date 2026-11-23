from __future__ import annotations

import asyncio

import pytest

from aiops_copilot.collectors import FixtureCollector
from aiops_copilot.models import Alert, IncidentContext
from aiops_copilot.pipeline import Pipeline
from aiops_copilot.preprocess import render_context, summarize_logs
from eval.run_eval import load_fixtures

# Cases the rules are expected to miss (the LLM has to read the logs).
RULE_GAPS = {"config-yaml-parse-error", "dependency-dns-enotfound"}


@pytest.mark.parametrize("case", load_fixtures(), ids=lambda c: c["name"])
def test_rules_baseline(case, settings, runbooks):
    ctx = IncidentContext.model_validate(case.get("context") or {})
    pipeline = Pipeline(settings, [FixtureCollector(ctx, case.get("workload"))], runbooks)
    incident = asyncio.run(pipeline.run(Alert.model_validate(case["alert"])))
    got = incident.analysis.category.value
    if case["name"] in RULE_GAPS:
        assert got == "unknown"
    else:
        assert got == case["expected_category"]
        assert incident.analysis.runbook_id == case.get("expected_runbook")


def test_drain_collapses_repeated_lines():
    lines = [f"ERROR dial tcp 10.0.0.{i}:5432: connect: connection refused" for i in range(200)]
    lines += [f"INFO handled request id={i} in {i % 50}ms" for i in range(300)]
    templates = summarize_logs(lines)
    assert len(templates) <= 3
    assert templates[0].is_error and templates[0].count == 200


def test_render_context_respects_budget():
    ctx = IncidentContext(events=[f"Warning BackOff Pod/x: event {i} " + "x" * 200 for i in range(100)])
    alert = Alert(labels={"alertname": "A", "namespace": "n"})
    from aiops_copilot.models import Target

    text = render_context(alert, Target(namespace="n"), ctx, [], budget_chars=1500)
    assert len(text) < 1700
    assert text.startswith("## Alert")
