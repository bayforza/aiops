"""Evidence-based confidence: how well the collected evidence supports a category.

The model's self-reported confidence is not trusted on its own. This score looks at what
was actually observed:

- Is there a strong, unambiguous signal for the category?
- Do independent sources (Kubernetes state, metrics, logs) point at the same cause?
- Did the rule-based analysis reach the same category?
- Did the model agree or disagree?

Week 3 adds two more inputs (past success of the fix, similar past incidents) once the
feedback store exists; `assess` already takes them as optional arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Category, RCAResult, Signal

# Signal kinds that support each category (mirrors the rule table in preprocess/signals.py).
CATEGORY_SIGNALS: dict[Category, set[str]] = {
    Category.OOM: {"oom_killed", "memory_near_limit", "crash_loop"},
    Category.IMAGE_PULL: {"image_pull"},
    Category.CONFIG_ERROR: {"config_error"},
    Category.DISK_FULL: {"disk_full"},
    Category.BAD_DEPLOY: {"recent_rollout", "crash_loop", "app_error"},
    Category.DEPENDENCY: {"dependency_down"},
    Category.PROBE_FAILURE: {"probe_failure"},
}


def signal_source(sig: Signal) -> str:
    """Which independent source a signal came from: k8s, metrics or logs."""
    if sig.detail.startswith("log:"):
        return "logs"
    if sig.kind == "memory_near_limit" or sig.detail.startswith("node filesystem"):
        return "metrics"
    return "k8s"


@dataclass
class EvidenceAssessment:
    score: float
    sources: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def sufficient(self, threshold: float) -> bool:
        return self.score >= threshold


def assess(category: Category, signals: list[Signal], rules: RCAResult, llm_category: Category | None = None,
           past_success_rate: float | None = None, similar_incidents: int = 0) -> EvidenceAssessment:
    if category == Category.UNKNOWN:
        return EvidenceAssessment(0.2, [], ["category could not be determined"])

    relevant = [s for s in signals if s.kind in CATEGORY_SIGNALS.get(category, set())]
    reasons: list[str] = []
    if not relevant:
        score = 0.25
        reasons.append("no deterministic signal supports this category")
    else:
        strongest = max(s.weight for s in relevant)
        score = 0.5 * strongest
        reasons.append(f"strongest signal {strongest:.1f} ({max(relevant, key=lambda s: s.weight).kind})")
    sources = sorted({signal_source(s) for s in relevant})
    if len(sources) > 1:
        bonus = min(0.3, 0.15 * (len(sources) - 1))
        score += bonus
        reasons.append(f"{len(sources)} independent sources point at the same cause: {', '.join(sources)}")

    if rules.category == category:
        score += 0.2
        reasons.append("rule-based analysis found the same category")
    elif rules.category != Category.UNKNOWN:
        score -= 0.1
        reasons.append(f"rule-based analysis differs: {rules.category.value}")

    if llm_category is not None:
        if llm_category == category:
            score += 0.1
            reasons.append("model found the same category")
        else:
            score -= 0.15
            reasons.append(f"model chose a different category: {llm_category.value}")

    if past_success_rate is not None:
        score += 0.2 * (past_success_rate - 0.5)
        reasons.append(f"past success rate of this fix {past_success_rate:.0%}")
    if similar_incidents:
        score += min(0.1, 0.05 * similar_incidents)
        reasons.append(f"{similar_incidents} similar past incidents")

    return EvidenceAssessment(round(max(0.0, min(0.95, score)), 2), sources, reasons)
