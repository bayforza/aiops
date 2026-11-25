from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import respx
from fastapi.testclient import TestClient

from aiops_copilot.audit import AuditLog
from aiops_copilot.collectors import FixtureCollector
from aiops_copilot.llm import ClaudeClient, LLMClient
from aiops_copilot.models import Alert, IncidentContext
from aiops_copilot.pipeline import Pipeline
from aiops_copilot.server import create_app
from aiops_copilot.stats import compute, percentile
from aiops_copilot.store import Store
from eval.run_eval import load_fixtures

A_URL, B_URL = "http://llm-a.test/v1", "http://llm-b.test/v1"


def case(name: str) -> dict:
    return next(c for c in load_fixtures() if c["name"] == name)


def pipeline_for(settings, runbooks, c, llms=None, cloud=None) -> Pipeline:
    ctx = IncidentContext.model_validate(c.get("context") or {})
    return Pipeline(settings, [FixtureCollector(ctx, c.get("workload"))], runbooks, llms=llms, cloud_llm=cloud,
                    audit=AuditLog(settings.audit_path))


def reply(obj: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(obj)}}],
                                     "usage": {"prompt_tokens": 700, "completion_tokens": 60}})


YAML_ANSWER = {"category": "config_error", "confidence": 0.8, "root_cause": "rules.yaml has a YAML syntax error.",
               "evidence": ["yaml: line 12"], "runbook_id": None}


@respx.mock
def test_second_local_model_answers_when_first_fails(settings, runbooks):
    respx.post(f"{A_URL}/chat/completions").mock(return_value=httpx.Response(500))
    respx.post(f"{B_URL}/chat/completions").mock(return_value=reply(YAML_ANSWER))
    llms = [LLMClient(A_URL, "qwen", name="qwen-7b"), LLMClient(B_URL, "gpt-oss", name="gpt-oss-20b")]
    c = case("config-yaml-parse-error")
    inc = asyncio.run(pipeline_for(settings, runbooks, c, llms).run(Alert.model_validate(c["alert"])))
    assert [x.name for x in inc.model_calls] == ["qwen-7b", "gpt-oss-20b"]
    assert [x.ok for x in inc.model_calls] == [False, True]
    assert inc.model_calls[1].prompt_tokens == 700
    assert inc.analysis.answered_by == {"name": "gpt-oss-20b", "model": "gpt-oss", "kind": "local"}
    for stage in ("collect", "preprocess", "llm", "act", "total", "collect.fixture"):
        assert stage in inc.timings_ms


def test_rules_only_answer_is_attributed_to_rules(settings, runbooks):
    c = case("oom-checkout-cache")
    inc = asyncio.run(pipeline_for(settings, runbooks, c).run(Alert.model_validate(c["alert"])))
    assert inc.analysis.answered_by["kind"] == "rules"
    assert inc.model_calls == []


def test_claude_client_uses_fallbacks_and_reads_text():
    calls = {}

    async def create(**kw):
        calls.update(kw)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                               content=[SimpleNamespace(type="text", text=json.dumps(YAML_ANSWER))],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=80))

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    client = ClaudeClient(client=fake, effort="low")
    answer, usage = asyncio.run(client.complete_json(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "ctx"}]))
    assert answer["category"] == "config_error"
    assert usage["prompt_tokens"] == 900
    assert calls["system"] == "sys" and calls["messages"] == [{"role": "user", "content": "ctx"}]
    assert calls["fallbacks"] == "default" and calls["output_config"] == {"effort": "low"}
    assert "temperature" not in calls


def test_claude_refusal_raises():
    async def create(**kw):
        return SimpleNamespace(stop_reason="refusal", content=[], usage=None)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    try:
        asyncio.run(ClaudeClient(client=fake).complete_json([{"role": "user", "content": "x"}]))
    except RuntimeError as exc:
        assert "declined" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_store_votes_and_alerts(settings, runbooks):
    store = Store(":memory:")
    c = case("oom-checkout-cache")
    alert = Alert.model_validate(c["alert"])
    aid = store.record_alert(alert, "queued")
    inc = asyncio.run(pipeline_for(settings, runbooks, c).run(alert))
    store.save_incident(inc)
    store.link_alert(aid, inc.id)
    store.add_feedback(inc.id, "partial", None)
    store.add_feedback(inc.id, "correct", "wrong", "limit was fine, it was a leak")
    v = store.latest_votes()[inc.id]
    assert v["root_cause"] == "correct" and v["fix"] == "wrong" and v["votes"] == 2
    assert store.alerts()[0]["decision"] == "analysed"
    assert store.set_status(inc.id, "resolved").status == "resolved"
    try:
        store.add_feedback(inc.id, "maybe", None)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid vote accepted")


def test_stats_accuracy_and_latency(settings, runbooks):
    store = Store(":memory:")
    ids = []
    for name in ("oom-checkout-cache", "image-pull-typo", "disk-full-logs"):
        c = case(name)
        inc = asyncio.run(pipeline_for(settings, runbooks, c).run(Alert.model_validate(c["alert"])))
        store.save_incident(inc)
        ids.append(inc.id)
    store.add_feedback(ids[0], "correct", "correct")
    store.add_feedback(ids[1], "partial", "wrong")
    s = compute(store.incidents(), store.latest_votes(), store.alerts())
    assert s["incidents"] == 3 and s["rated"] == 2
    assert s["root_cause"]["accuracy"] == 0.75 and s["fix"]["accuracy"] == 0.5
    assert s["answered_by"][0]["name"] == "rules" and s["answered_by"][0]["incidents"] == 3
    assert s["latency_ms"]["total"]["n"] == 3
    assert percentile([10, 20, 30, 40], 50) == 25.0


def test_ui_api_end_to_end(settings, runbooks):
    c = case("oom-checkout-cache")
    store = Store(":memory:")
    app = create_app(pipeline_for(settings, runbooks, c), store=store)
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
        assert "AIOps Copilot" in client.get("/ui/").text
        payload = {"alerts": [c["alert"] | {"fingerprint": "fp-1"}]}
        assert client.post("/webhook/alertmanager", json=payload).json()["accepted"]
        assert client.post("/webhook/alertmanager", json=payload).json()["skipped"]  # deduplicated
        for _ in range(50):
            items = client.get("/api/incidents").json()
            if items:
                break
            time.sleep(0.05)
        inc_id = items[0]["id"]
        assert items[0]["answered_by"]["kind"] == "rules"
        detail = client.get(f"/api/incidents/{inc_id}").json()
        assert detail["runbook"]["id"] == "oom-increase-memory" and detail["runbook"]["steps"]
        assert any(e["event"] == "analysis_done" for e in detail["timeline"])
        assert detail["incident"]["received_at"]

        assert client.post(f"/api/incidents/{inc_id}/feedback", json={}).status_code == 422
        assert client.post(f"/api/incidents/{inc_id}/feedback", json={"root_cause": "nope"}).status_code == 422
        r = client.post(f"/api/incidents/{inc_id}/feedback", json={"root_cause": "correct", "fix": "partial",
                                                                    "author": "ops"})
        assert r.status_code == 201
        assert client.post(f"/api/incidents/{inc_id}/status", json={"status": "acknowledged"}).json()["status"] \
            == "acknowledged"
        assert client.get("/api/incidents?status=acknowledged").json()
        assert client.get("/api/incidents?q=no-such-thing").json() == []

        stats = client.get("/api/stats").json()
        assert stats["root_cause"]["accuracy"] == 1.0 and stats["fix"]["accuracy"] == 0.5
        assert stats["alerts"]["deduplicated"] == 1
        alerts = client.get("/api/alerts").json()
        assert {a["decision"] for a in alerts} == {"analysed", "deduplicated"}

        resolved = {"alerts": [c["alert"] | {"fingerprint": "fp-1", "status": "resolved"}]}
        client.post("/webhook/alertmanager", json=resolved)
        assert client.get(f"/api/incidents/{inc_id}").json()["incident"]["status"] == "resolved"
        info = client.get("/api/info").json()
        assert info["cloud"]["enabled"] is False and info["local_models"] == []
        assert client.get("/api/incidents/nope").status_code == 404
