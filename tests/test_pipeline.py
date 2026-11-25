from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

from aiops_copilot.actions import GitOpsActor
from aiops_copilot.audit import AuditLog
from aiops_copilot.collectors import FixtureCollector
from aiops_copilot.llm import LLMClient
from aiops_copilot.models import Alert, IncidentContext
from aiops_copilot.pipeline import Pipeline
from aiops_copilot.server import create_app
from eval.run_eval import load_fixtures

LLM_URL = "http://llm.test/v1"


def case(name: str) -> dict:
    return next(c for c in load_fixtures() if c["name"] == name)


def make_pipeline(settings, runbooks, c: dict, llm: LLMClient | None = None, actor=None) -> Pipeline:
    ctx = IncidentContext.model_validate(c.get("context") or {})
    return Pipeline(settings, [FixtureCollector(ctx, c.get("workload"))], runbooks, llm=llm,
                    actor=actor, audit=AuditLog(settings.audit_path))


def llm_reply(obj: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(obj)}}],
                                     "usage": {"prompt_tokens": 900, "completion_tokens": 80}})


@respx.mock
def test_llm_fills_rule_gap(settings, runbooks):
    route = respx.post(f"{LLM_URL}/chat/completions").mock(return_value=llm_reply({
        "category": "config_error", "confidence": 0.8,
        "root_cause": "rules.yaml dosyasında YAML sözdizimi hatası var.",
        "evidence": ["yaml: line 12: mapping values are not allowed"], "runbook_id": None}))
    c = case("config-yaml-parse-error")
    inc = asyncio.run(make_pipeline(settings, runbooks, c, LLMClient(LLM_URL, "m")).run(
        Alert.model_validate(c["alert"])))
    assert inc.analysis.category.value == "config_error"
    assert inc.analysis.source == "llm"
    assert inc.analysis.runbook_id == "config-error"  # filled from category
    assert inc.analysis.remediation.kind == "manual"
    body = json.loads(route.calls[0].request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert "yaml: line 12" in body["messages"][1]["content"]


@respx.mock
def test_llm_failure_falls_back_to_rules(settings, runbooks):
    respx.post(f"{LLM_URL}/chat/completions").mock(return_value=httpx.Response(500))
    c = case("oom-checkout-cache")
    inc = asyncio.run(make_pipeline(settings, runbooks, c, LLMClient(LLM_URL, "m")).run(
        Alert.model_validate(c["alert"])))
    assert inc.analysis.category.value == "oom"
    assert any("LLM kullanılamadı" in n for n in inc.analysis.notes)
    ok, _ = AuditLog(settings.audit_path).verify()
    assert ok


def test_level1_dry_run_writes_patch(settings, runbooks):
    settings.policy.autonomy_level = 1
    actor = GitOpsActor(settings.gitops, settings.output_dir)
    c = case("oom-checkout-cache")
    inc = asyncio.run(make_pipeline(settings, runbooks, c, actor=actor).run(Alert.model_validate(c["alert"])))
    act = inc.actions[0]
    assert act.kind == "dry_run_patch" and act.status == "written"
    assert "memory: 96Mi" in act.diff
    assert (Path(settings.output_dir) / f"{inc.id}-pr.md").exists()


def test_level0_only_previews(settings, runbooks):
    settings.policy.autonomy_level = 0
    settings.gitops.dry_run = False  # even with PRs enabled in gitops config, level 0 must not open one
    actor = GitOpsActor(settings.gitops, settings.output_dir)
    c = case("oom-checkout-cache")
    inc = asyncio.run(make_pipeline(settings, runbooks, c, actor=actor).run(Alert.model_validate(c["alert"])))
    assert inc.actions[0].kind == "dry_run_patch"
    assert any("PR açılmadı" in n for n in inc.analysis.notes)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@respx.mock
def test_pull_request_mode(settings, runbooks, tmp_path, root, monkeypatch):
    # A local bare repo stands in for GitHub's git remote; the REST call is mocked.
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    shutil.copytree(root / "gitops", work / "gitops")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    _git(work, "push", "origin", "HEAD:main")

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    pr = respx.post("https://api.github.com/repos/bayforza/aiops/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/bayforza/aiops/pull/1"}))
    settings.policy.autonomy_level = 1
    settings.gitops.dry_run = False
    settings.gitops.repo_path = str(work)
    c = case("oom-checkout-cache")
    inc = asyncio.run(make_pipeline(settings, runbooks, c, actor=GitOpsActor(settings.gitops,
                                    settings.output_dir)).run(Alert.model_validate(c["alert"])))
    act = inc.actions[0]
    assert act.status == "opened", act.detail
    assert act.url.endswith("/pull/1")
    req = json.loads(pr.calls[0].request.content)
    assert req["head"] == f"aiops/{inc.id}" and req["base"] == "main"
    assert "insan onayı olmadan merge edilmemelidir" in req["body"]
    branches = subprocess.run(["git", "-C", str(remote), "branch"], capture_output=True, text=True).stdout
    assert f"aiops/{inc.id}" in branches
    # The operator's checkout is untouched.
    assert "memory: 64Mi" in (work / "gitops/apps/checkout-api/deployment.yaml").read_text()


def test_webhook_dedupes_and_records(settings, runbooks):
    c = case("image-pull-typo")
    app = create_app(make_pipeline(settings, runbooks, c))
    payload = {"status": "firing", "alerts": [dict(c["alert"], fingerprint="abc")]}
    with TestClient(app) as client:
        r1 = client.post("/webhook/alertmanager", json=payload)
        r2 = client.post("/webhook/alertmanager", json=payload)
        assert r1.status_code == 202 and r1.json()["accepted"] == ["KubeContainerWaiting"]
        assert r2.json()["skipped"] == ["KubeContainerWaiting"]
        for _ in range(50):
            items = client.get("/incidents").json()
            if items:
                break
            time.sleep(0.05)
        assert items[0]["category"] == "image_pull"
        detail = client.get(f"/incidents/{items[0]['id']}").json()
        assert detail["analysis"]["remediation"]["params"]["image"] == "nginx:1.27-alpine"


CLOUD_URL = "http://cloud.test/v1"


def cloud_settings(settings, monkeypatch, **kw):
    monkeypatch.setenv("AIOPS_CLOUD_API_KEY", "test-key")
    settings.cloud = settings.cloud.model_copy(update={"enabled": True, "base_url": CLOUD_URL, **kw})
    return settings


def run_with_cloud(settings, runbooks, name: str) -> object:
    c = case(name)
    ctx = IncidentContext.model_validate(c.get("context") or {})
    pipeline = Pipeline(settings, [FixtureCollector(ctx, c.get("workload"))], runbooks,
                        cloud_llm=LLMClient(CLOUD_URL, "cloud-model"), audit=AuditLog(settings.audit_path))
    return asyncio.run(pipeline.run(Alert.model_validate(c["alert"])))


@respx.mock
def test_uncertain_incident_gets_anonymised_cloud_opinion(settings, runbooks, monkeypatch):
    cloud_settings(settings, monkeypatch)
    route = respx.post(f"{CLOUD_URL}/chat/completions").mock(return_value=llm_reply({
        "category": "dependency", "confidence": 0.9,
        "root_cause": "WORKLOAD_1 yavaş bir alt servisi bekliyor.", "evidence": [], "runbook_id": None}))
    inc = run_with_cloud(settings, runbooks, "latency-no-signal")
    sent = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "orders-api" not in sent and "shop" not in sent and "WORKLOAD_1" in sent
    a = inc.analysis
    assert a.route == "cloud"
    assert a.second_opinion["sent_payload"] == sent
    assert a.category.value == "dependency" and a.source.startswith("cloud")
    assert "orders-api" in a.root_cause  # mapped back for the operator
    assert a.confidence <= 0.3  # unsupported by evidence, so no PR on the cloud's word alone
    events = [json.loads(line)["event"] for line in Path(settings.audit_path).read_text().splitlines()]
    assert "route_decided" in events and "cloud_called" in events


@respx.mock
def test_strong_local_result_and_local_only_namespace_never_call_cloud(settings, runbooks, monkeypatch):
    route = respx.post(f"{CLOUD_URL}/chat/completions").mock(return_value=llm_reply({}))
    inc = run_with_cloud(cloud_settings(settings, monkeypatch), runbooks, "oom-checkout-cache")
    assert inc.analysis.route == "local" and inc.analysis.second_opinion is None
    cloud_settings(settings, monkeypatch, local_only_namespaces=["shop"])
    inc = run_with_cloud(settings, runbooks, "latency-no-signal")
    assert inc.analysis.route == "local_only"
    assert not route.called


@respx.mock
def test_cloud_failure_keeps_local_result(settings, runbooks, monkeypatch):
    respx.post(f"{CLOUD_URL}/chat/completions").mock(return_value=httpx.Response(503))
    inc = run_with_cloud(cloud_settings(settings, monkeypatch), runbooks, "latency-no-signal")
    assert inc.analysis.category.value == "unknown"
    assert any("Cloud ikinci görüş alınamadı" in n for n in inc.analysis.notes)
