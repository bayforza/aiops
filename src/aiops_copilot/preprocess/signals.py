"""Deterministic signal detection.

Rules run before the LLM, in milliseconds on any CPU. They give the model a
short list of facts to reason over, and they are the fallback answer when the
model is unavailable, slow or returns something unusable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..models import Category, IncidentContext, RCAResult, Signal

_IMAGE_PULL = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}
_CONFIG = {"CreateContainerConfigError", "CreateContainerError"}

_LOG_RULES: list[tuple[str, re.Pattern[str], float]] = [
    ("disk_full", re.compile(r"no space left on device|disk (is )?full|ENOSPC", re.I), 0.9),
    ("config_error", re.compile(
        r"(missing|required).{0,40}(env|environment|variable|config)|"
        r"(env|environment variable|config(uration)?).{0,40}(not set|missing|not found|invalid)", re.I), 0.7),
    ("dependency_down", re.compile(
        r"connection refused|ECONNREFUSED|could not connect|connect(ion)? timed? ?out|"
        r"no such host|name or service not known|dial tcp", re.I), 0.6),
    ("app_error", re.compile(r"\bpanic\b|Traceback \(most recent call last\)|Unhandled exception|"
                             r"\bFATAL\b|segmentation fault", re.I), 0.5),
]

_EVENT_RULES: list[tuple[str, re.Pattern[str], float]] = [
    ("oom_killed", re.compile(r"OOMKill", re.I), 1.0),
    ("probe_failure", re.compile(r"(Readiness|Liveness|Startup) probe failed", re.I), 0.7),
    ("disk_full", re.compile(r"DiskPressure|ephemeral-storage|evicted.*storage", re.I), 0.8),
    ("image_pull", re.compile(r"Failed to pull image|ErrImagePull|ImagePullBackOff", re.I), 1.0),
]


def _add(signals: dict[str, Signal], kind: str, detail: str, weight: float, **data) -> None:
    current = signals.get(kind)
    if current is None or weight > current.weight:
        signals[kind] = Signal(kind=kind, detail=detail[:300], weight=weight, data=data)


def detect_signals(ctx: IncidentContext, reference: datetime, window_minutes: int = 30) -> list[Signal]:
    s: dict[str, Signal] = {}

    for c in ctx.containers:
        if c.last_terminated_reason == "OOMKilled":
            _add(s, "oom_killed", f"container {c.name} last terminated OOMKilled "
                 f"(limit {c.memory_limit or '?'}, restarts {c.restart_count})", 1.0,
                 container=c.name, memory_limit=c.memory_limit)
        if c.waiting_reason in _IMAGE_PULL:
            _add(s, "image_pull", f"container {c.name} {c.waiting_reason}: {c.waiting_message or c.image}",
                 1.0, container=c.name, image=c.image)
        if c.waiting_reason in _CONFIG:
            _add(s, "config_error", f"container {c.name} {c.waiting_reason}: {c.waiting_message or ''}", 0.9,
                 container=c.name)
        if c.waiting_reason == "CrashLoopBackOff" or c.restart_count >= 3:
            _add(s, "crash_loop", f"container {c.name} restarting (restarts {c.restart_count}, "
                 f"last exit {c.last_exit_code})", 0.6, container=c.name)

    for ev in ctx.events:
        for kind, rx, w in _EVENT_RULES:
            if rx.search(ev):
                _add(s, kind, ev, w)

    for line in ctx.log_lines:
        for kind, rx, w in _LOG_RULES:
            if rx.search(line):
                _add(s, kind, f"log: {line.strip()}", w)

    for m in ctx.metrics:
        if m.name == "memory_usage_vs_limit" and m.max is not None and m.max >= 0.95:
            _add(s, "memory_near_limit", f"memory working set reached {m.max:.0%} of limit", 0.7)
        if m.name == "node_disk_used" and m.max is not None and m.max >= 0.9:
            _add(s, "disk_full", f"node filesystem {m.max:.0%} used", 0.8)

    rollouts = sorted(
        (c for c in ctx.changes if c.source == "k8s-rollout" and c.revision),
        key=lambda c: int(c.revision or 0),
    )
    if len(rollouts) >= 2:
        latest, previous = rollouts[-1], rollouts[-2]
        recent = latest.at is None or latest.at >= reference - timedelta(minutes=window_minutes)
        if recent and latest.image != previous.image:
            _add(s, "recent_rollout", f"revision {latest.revision} changed image {previous.image} -> "
                 f"{latest.image}", 0.6, previous_image=previous.image, new_image=latest.image)

    return sorted(s.values(), key=lambda x: -x.weight)


# Ordered: first rule that matches wins.
_RULES: list[tuple[Category, set[str], set[str]]] = [
    # category, all required signals, any-of signals (empty = none needed)
    (Category.IMAGE_PULL, {"image_pull"}, set()),
    (Category.OOM, {"oom_killed"}, set()),
    (Category.CONFIG_ERROR, {"config_error"}, set()),
    (Category.DISK_FULL, {"disk_full"}, set()),
    (Category.BAD_DEPLOY, {"crash_loop", "recent_rollout"}, set()),
    (Category.DEPENDENCY, {"dependency_down"}, set()),
    (Category.OOM, {"memory_near_limit"}, {"crash_loop"}),
    (Category.PROBE_FAILURE, {"probe_failure"}, set()),
]

_SUMMARY_TR = {
    Category.IMAGE_PULL: "Container imajı çekilemiyor; imaj adı/etiketi ya da registry erişimi hatalı.",
    Category.OOM: "Container bellek limitini aştığı için OOMKilled ile sonlandırılıyor.",
    Category.CONFIG_ERROR: "Uygulama eksik ya da hatalı yapılandırma (env/ConfigMap/Secret) yüzünden başlayamıyor.",
    Category.DISK_FULL: "Disk alanı dolmuş; uygulama veya node yazamıyor.",
    Category.BAD_DEPLOY: "Son deploy ile gelen yeni imaj sonrasında uygulama çöküyor; değişiklik kaynaklı hata.",
    Category.DEPENDENCY: "Uygulama bağımlı olduğu bir servise (DB, API, DNS) bağlanamıyor.",
    Category.PROBE_FAILURE: "Readiness/liveness probe başarısız; uygulama zamanında sağlıklı yanıt vermiyor.",
    Category.UNKNOWN: "Kesin bir kök neden sinyali bulunamadı; manuel inceleme gerekiyor.",
}


def deterministic_rca(signals: list[Signal]) -> RCAResult:
    kinds = {sig.kind for sig in signals}
    for category, required, any_of in _RULES:
        if required <= kinds and (not any_of or any_of & kinds):
            used = [sig for sig in signals if sig.kind in required | any_of]
            confidence = min(0.95, max(sig.weight for sig in used) * 0.9)
            return RCAResult(
                category=category,
                root_cause=_SUMMARY_TR[category],
                confidence=round(confidence, 2),
                evidence=[sig.detail for sig in used],
                source="deterministic",
            )
    return RCAResult(
        category=Category.UNKNOWN,
        root_cause=_SUMMARY_TR[Category.UNKNOWN],
        confidence=0.2,
        evidence=[sig.detail for sig in signals[:3]],
        source="deterministic",
    )
