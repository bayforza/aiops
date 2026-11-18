"""Runbook store: the organisation's known fixes, matched to detected signals.

The model is asked to *choose* among matching runbooks rather than invent a
fix. Small local models are far more reliable at selection than at free-form
remediation, and every proposed change stays traceable to a reviewed runbook.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .models import Alert, Category, Signal


class RunbookMatch(BaseModel):
    signals: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)


class RunbookRemediation(BaseModel):
    kind: str = "manual"  # gitops_patch | manual
    action: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class Runbook(BaseModel):
    id: str
    title: str
    category: Category
    match: RunbookMatch = Field(default_factory=RunbookMatch)
    description: str = ""
    steps: list[str] = Field(default_factory=list)
    remediation: RunbookRemediation = Field(default_factory=RunbookRemediation)


class RunbookStore:
    def __init__(self, runbooks: list[Runbook]):
        self.runbooks = {rb.id: rb for rb in runbooks}

    @classmethod
    def from_dir(cls, path: str | Path) -> RunbookStore:
        books = []
        for f in sorted(Path(path).glob("*.y*ml")):
            data = yaml.safe_load(f.read_text())
            for item in data if isinstance(data, list) else [data]:
                books.append(Runbook.model_validate(item))
        return cls(books)

    def get(self, runbook_id: str | None) -> Runbook | None:
        return self.runbooks.get(runbook_id) if runbook_id else None

    def for_category(self, category: Category) -> Runbook | None:
        for rb in self.runbooks.values():
            if rb.category == category:
                return rb
        return None

    def match(self, signals: list[Signal], alert: Alert, limit: int = 3) -> list[Runbook]:
        weights = {s.kind: s.weight for s in signals}
        scored = []
        for rb in self.runbooks.values():
            score = sum(weights.get(k, 0.0) for k in rb.match.signals)
            if alert.name in rb.match.alerts:
                score += 0.3
            if score > 0:
                scored.append((score, rb))
        scored.sort(key=lambda x: -x[0])
        return [rb for _, rb in scored[:limit]]
