"""Measure RCA accuracy and latency of the configured local model on recorded incidents.

    python -m eval.run_eval -c config.yaml           # rules + LLM
    python -m eval.run_eval -c config.yaml --no-llm  # rules only (baseline)

This table is the number to show customers: "on N incident types, model X on hardware Y
finds the right root cause in Z% of cases in W seconds".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import time
from pathlib import Path

import yaml

from aiops_copilot.collectors import FixtureCollector
from aiops_copilot.config import load_settings
from aiops_copilot.factory import build_cloud_client
from aiops_copilot.llm import LLMClient
from aiops_copilot.models import Alert, IncidentContext
from aiops_copilot.pipeline import Pipeline
from aiops_copilot.runbooks import RunbookStore
from aiops_copilot.store import Store

FIXTURES = Path(__file__).with_name("fixtures.yaml")


def load_fixtures(path: Path = FIXTURES) -> list[dict]:
    return yaml.safe_load(path.read_text())


async def run_case(case: dict, settings, runbooks: RunbookStore, llm: LLMClient | None,
                   cloud_llm: LLMClient | None = None, store: Store | None = None) -> dict:
    ctx = IncidentContext.model_validate(case.get("context") or {})
    pipeline = Pipeline(settings, [FixtureCollector(ctx, case.get("workload"))], runbooks, llm=llm,
                        cloud_llm=cloud_llm)
    t0 = time.perf_counter()
    alert = Alert.model_validate(case["alert"])
    alert_id = store.record_alert(alert, "queued") if store else None
    incident = await pipeline.run(alert)
    if store:
        store.save_incident(incident)
        store.link_alert(alert_id, incident.id)
    a = incident.analysis
    return {
        "name": case["name"],
        "expected": case["expected_category"],
        "got": a.category.value,
        "ok": a.category.value == case["expected_category"],
        "runbook_ok": a.runbook_id == case.get("expected_runbook"),
        "confidence": a.confidence,
        "source": a.source,
        "evidence_score": a.evidence_score,
        "route": a.route,
        "cloud_called": a.second_opinion is not None,
        "cloud_agreed": (a.second_opinion or {}).get("agrees_with_local"),
        "seconds": round(time.perf_counter() - t0, 2),
        "root_cause": a.root_cause,
        "notes": a.notes,
    }


async def main_async(args) -> list[dict]:
    settings = load_settings(args.config)
    settings = settings.model_copy(update={"output_dir": "out/eval"})
    runbooks = RunbookStore.from_dir(settings.runbooks_dir)
    llm = None
    if not args.no_llm and settings.llm.enabled:
        c = settings.llm
        llm = LLMClient(c.base_url, c.model, c.api_key, c.timeout_s, c.temperature, c.max_tokens)
    cloud_llm = build_cloud_client(settings.cloud, settings.llm) if args.cloud else None
    store = Store(args.store) if args.store else None
    cases = load_fixtures()
    if args.only:
        cases = [c for c in cases if args.only in c["name"]]
    results = []
    for case in cases:  # sequential: measures per-incident latency as a CPU host would see it
        r = await run_case(case, settings, runbooks, llm, cloud_llm, store)
        results.append(r)
        mark = "OK " if r["ok"] else "XX "
        print(f"{mark} {r['name']:<28} expected={r['expected']:<14} got={r['got']:<14} "
              f"conf={r['confidence']:.2f} ev={r['evidence_score'] or 0:.2f} src={r['source']:<18} "
              f"route={r['route']:<10} {r['seconds']:>6.1f}s")
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-c", "--config", default=None)
    p.add_argument("--no-llm", action="store_true", help="rules only")
    p.add_argument("--cloud", action="store_true",
                   help="allow cloud second opinions (needs cloud.enabled and the API key env var)")
    p.add_argument("--store", help="also save the incidents to this UI database (e.g. out/aiops.db)")
    p.add_argument("--only", help="run cases whose name contains this")
    args = p.parse_args()
    results = asyncio.run(main_async(args))
    n = len(results)
    acc = sum(r["ok"] for r in results) / n if n else 0
    rb = sum(r["runbook_ok"] for r in results) / n if n else 0
    secs = sorted(r["seconds"] for r in results)
    settings = load_settings(args.config)
    summary = {
        "model": None if args.no_llm else settings.llm.model,
        "host": f"{platform.system()} {platform.machine()}",
        "cases": n,
        "category_accuracy": round(acc, 3),
        "runbook_accuracy": round(rb, 3),
        "median_seconds": secs[n // 2] if n else 0,
        "max_seconds": secs[-1] if n else 0,
        # Week 2 (hybrid AI): how often the cloud would be / was asked, and whether it changed anything.
        "mean_evidence_score": round(sum(r["evidence_score"] or 0 for r in results) / n, 3) if n else 0,
        "cloud_route_rate": round(sum(r["route"] == "cloud" for r in results) / n, 3) if n else 0,
        "cloud_called": sum(r["cloud_called"] for r in results),
        "cloud_agreed": sum(bool(r["cloud_agreed"]) for r in results),
    }
    print("\n" + json.dumps(summary, indent=2))
    out = Path("out/eval")
    out.mkdir(parents=True, exist_ok=True)
    tag = "rules" if args.no_llm else settings.llm.model.replace(":", "_").replace("/", "_")
    (out / f"results-{tag}.json").write_text(
        json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
