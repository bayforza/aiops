"""Compress raw log lines into a handful of templates with Drain3.

A 500-line log window usually collapses into 5-20 templates, which is what
makes a small CPU-hosted model usable: it reads patterns, not raw logs.
"""

from __future__ import annotations

import re

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

from ..models import LogTemplate

ERROR_RE = re.compile(
    r"\b(error|err|fatal|panic|exception|traceback|failed|failure|refused|timeout|oom|killed|denied|"
    r"no space left|cannot|could not|unable)\b",
    re.IGNORECASE,
)

_MASKS = [
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "UUID"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "IP"),
    (r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b", "TS"),
    (r"\b0x[0-9a-fA-F]+\b", "HEX"),
    (r"(?<![A-Za-z])\d+(?:\.\d+)?(?:ms|s|Mi|Gi|MB|GB|KB|%)?(?![A-Za-z])", "NUM"),
]


def _miner() -> TemplateMiner:
    cfg = TemplateMinerConfig()
    cfg.profiling_enabled = False
    cfg.drain_sim_th = 0.4
    cfg.drain_depth = 4
    cfg.masking_instructions = [MaskingInstruction(p, m) for p, m in _MASKS]
    return TemplateMiner(config=cfg)


def summarize_logs(lines: list[str], top: int = 15) -> list[LogTemplate]:
    if not lines:
        return []
    miner = _miner()
    samples: dict[int, str] = {}
    errors: dict[int, bool] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        res = miner.add_log_message(line[:2000])
        cid = res["cluster_id"]
        is_err = bool(ERROR_RE.search(line))
        # Keep the most recent sample, preferring an error line.
        if is_err or cid not in samples or not errors.get(cid):
            samples[cid] = line[:400]
        errors[cid] = errors.get(cid, False) or is_err
    templates = [
        LogTemplate(
            template=c.get_template()[:400],
            count=c.size,
            sample=samples.get(c.cluster_id, ""),
            is_error=errors.get(c.cluster_id, False),
        )
        for c in miner.drain.clusters
    ]
    # Errors first, then by frequency.
    templates.sort(key=lambda t: (not t.is_error, -t.count))
    return templates[:top]
