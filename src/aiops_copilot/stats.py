"""Numbers for the dashboard: accuracy from operator votes, model usage and latency per stage."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .models import Incident
from .store import VOTES

STAGES = ("queue", "collect", "preprocess", "llm", "cloud", "act", "total")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    k = (len(vals) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 1)


def latency(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
            "max": max(values) if values else None,
            "avg": round(sum(values) / len(values), 1) if values else None}


class _Acc:
    """Accuracy from votes: correct = 1, partial = 0.5, wrong = 0."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def add(self, vote: str | None) -> None:
        if vote in VOTES:
            self.counts[vote] += 1

    def out(self) -> dict[str, Any]:
        n = sum(self.counts.values())
        score = sum(VOTES[k] * v for k, v in self.counts.items()) / n if n else None
        return {"rated": n, "accuracy": round(score, 3) if score is not None else None,
                "correct": self.counts["correct"], "partial": self.counts["partial"], "wrong": self.counts["wrong"]}


def compute(incidents: list[Incident], votes: dict[str, dict[str, Any]], alerts: list[dict[str, Any]]
            ) -> dict[str, Any]:
    rc, fix = _Acc(), _Acc()
    by_answerer: dict[str, dict[str, Any]] = {}
    by_category: dict[str, dict[str, Any]] = {}
    calls: dict[str, dict[str, Any]] = {}
    stages: dict[str, list[float]] = defaultdict(list)
    routes: Counter[str] = Counter()
    status: Counter[str] = Counter()
    per_day: Counter[str] = Counter()
    conf_by_vote: dict[str, list[float]] = defaultdict(list)
    cloud_agreed = cloud_asked = 0

    for inc in incidents:
        a = inc.analysis
        v = votes.get(inc.id, {})
        status[inc.status] += 1
        routes[a.route] += 1
        per_day[inc.created_at.strftime("%Y-%m-%d")] += 1
        rc.add(v.get("root_cause"))
        fix.add(v.get("fix"))
        if v.get("root_cause") in VOTES:
            conf_by_vote[v["root_cause"]].append(a.confidence)

        who = a.answered_by or {"name": "rules", "kind": "rules", "model": ""}
        row = by_answerer.setdefault(who["name"], {"name": who["name"], "kind": who["kind"],
                                                   "model": who.get("model", ""), "incidents": 0,
                                                   "_rc": _Acc(), "_fix": _Acc(), "_conf": [], "_ms": []})
        row["incidents"] += 1
        row["_rc"].add(v.get("root_cause"))
        row["_fix"].add(v.get("fix"))
        row["_conf"].append(a.confidence)
        row["_ms"].append(inc.timings_ms.get("total", 0))

        cat = by_category.setdefault(a.category.value, {"category": a.category.value, "incidents": 0, "_rc": _Acc()})
        cat["incidents"] += 1
        cat["_rc"].add(v.get("root_cause"))

        for st in STAGES:
            if st in inc.timings_ms:
                stages[st].append(inc.timings_ms[st])
        for c in inc.model_calls:
            m = calls.setdefault(c.name, {"name": c.name, "model": c.model, "kind": c.kind, "calls": 0, "ok": 0,
                                          "_ms": [], "_in": [], "_out": []})
            m["calls"] += 1
            m["ok"] += int(c.ok)
            if c.ok:
                m["_ms"].append(c.ms)
                if c.prompt_tokens is not None:
                    m["_in"].append(c.prompt_tokens)
                if c.completion_tokens is not None:
                    m["_out"].append(c.completion_tokens)
        if a.second_opinion:
            cloud_asked += 1
            cloud_agreed += int(bool(a.second_opinion.get("agrees_with_local")))

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 3) if xs else None

    answerers = []
    for row in by_answerer.values():
        answerers.append({k: v for k, v in row.items() if not k.startswith("_")} | {
            "root_cause": row["_rc"].out(), "fix": row["_fix"].out(), "avg_confidence": avg(row["_conf"]),
            "latency_ms": latency(row["_ms"])})
    models = []
    for m in calls.values():
        models.append({k: v for k, v in m.items() if not k.startswith("_")} | {
            "success_rate": round(m["ok"] / m["calls"], 3) if m["calls"] else None,
            "latency_ms": latency(m["_ms"]), "avg_prompt_tokens": avg(m["_in"]),
            "avg_completion_tokens": avg(m["_out"])})
    n = len(incidents)
    decisions = Counter(al["decision"] for al in alerts)
    return {
        "incidents": n,
        "status": dict(status),
        "alerts": {"received": len(alerts), **dict(decisions)},
        "rated": len([i for i in incidents if votes.get(i.id)]),
        "root_cause": rc.out(),
        "fix": fix.out(),
        "answered_by": sorted(answerers, key=lambda r: -r["incidents"]),
        "models": sorted(models, key=lambda r: -r["calls"]),
        "categories": sorted(({"category": c["category"], "incidents": c["incidents"], "root_cause": c["_rc"].out()}
                              for c in by_category.values()), key=lambda r: -r["incidents"]),
        "latency_ms": {st: latency(stages[st]) for st in STAGES},
        "routes": dict(routes),
        "cloud": {"asked": cloud_asked, "agreed": cloud_agreed,
                  "rate": round(cloud_asked / n, 3) if n else None},
        "confidence_by_vote": {k: avg(v) for k, v in conf_by_vote.items()},
        "per_day": dict(sorted(per_day.items())),
    }
