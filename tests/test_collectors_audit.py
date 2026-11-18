from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from aiops_copilot.audit import AuditLog
from aiops_copilot.collectors import LokiCollector, PrometheusCollector, Window
from aiops_copilot.collectors.base import pod_selector, workload_from_pod
from aiops_copilot.models import Alert, IncidentContext, Target

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
WIN = Window(NOW - timedelta(minutes=30), NOW)
TARGET = Target(namespace="demo", pod="checkout-api-6f7c9d8b5c-k2x9p", workload="checkout-api")


def test_workload_from_pod():
    assert workload_from_pod("checkout-api-6f7c9d8b5c-k2x9p") == "checkout-api"
    assert workload_from_pod("standalone") is None
    assert pod_selector(TARGET) == r"checkout\-api-.*"


@respx.mock
def test_prometheus_summarises_series():
    respx.get("http://prom/api/v1/query_range").mock(return_value=httpx.Response(200, json={
        "status": "success",
        "data": {"result": [{"values": [[1, "0.5"], [2, "0.99"], [3, "NaN"], [4, "0.7"]]}]}}))
    ctx = IncidentContext()
    asyncio.run(PrometheusCollector("http://prom").collect(Alert(), TARGET, WIN, ctx))
    mem = next(m for m in ctx.metrics if m.name == "memory_usage_vs_limit")
    assert (mem.min, mem.max, mem.last) == (0.5, 0.99, 0.7)
    assert 'namespace="demo"' in mem.query


@respx.mock
def test_prometheus_errors_are_recorded_not_raised():
    respx.get("http://prom/api/v1/query_range").mock(return_value=httpx.Response(503))
    ctx = IncidentContext()
    asyncio.run(PrometheusCollector("http://prom").collect(Alert(), TARGET, WIN, ctx))
    assert ctx.metrics == [] and len(ctx.errors) == 4


@respx.mock
def test_loki_orders_lines():
    route = respx.get("http://loki/loki/api/v1/query_range").mock(return_value=httpx.Response(200, json={
        "data": {"result": [{"values": [["3", "third"], ["1", "first"]]}, {"values": [["2", "second"]]}]}}))
    ctx = IncidentContext()
    asyncio.run(LokiCollector("http://loki").collect(Alert(), TARGET, WIN, ctx))
    assert ctx.log_lines == ["first", "second", "third"]
    assert "checkout" in route.calls[0].request.url.params["query"]


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append("alert_received", "i1", alert="A")
    log.append("analysis_done", "i1", category="oom")
    assert log.verify()[0]
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    rec = json.loads(lines[1])
    rec["data"]["category"] = "dependency"
    lines[1] = json.dumps(rec)
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n")
    ok, msg = log.verify()
    assert not ok and "line 2" in msg
