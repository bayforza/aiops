"""Minimal, reviewable edits to Kubernetes manifests in a GitOps repository.

Edits are round-tripped with ruamel.yaml so comments and formatting survive
and the resulting PR diff only shows the lines that really changed.
"""

from __future__ import annotations

import difflib
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


class PatchError(Exception):
    pass


@dataclass
class PatchResult:
    path: Path
    original: str
    updated: str
    summary: str

    @property
    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.original.splitlines(keepends=True),
                self.updated.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        )


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


_UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
_QTY = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]i?)?$")


def parse_quantity(q: str | int) -> int:
    m = _QTY.match(str(q).strip())
    if not m:
        raise PatchError(f"unsupported memory quantity: {q!r}")
    return int(float(m.group(1)) * _UNITS.get(m.group(2) or "", 1))


def format_quantity(n: int) -> str:
    mi = math.ceil(n / 2**20)
    return f"{mi // 1024}Gi" if mi % 1024 == 0 else f"{mi}Mi"


def find_deployment(root: Path, workload: str, namespace: str | None = None) -> tuple[Path, int]:
    y = _yaml()
    for path in sorted(root.rglob("*.y*ml")):
        try:
            docs = list(y.load_all(path.read_text()))
        except Exception:  # noqa: BLE001 - skip files that are not plain YAML (templates etc.)
            continue
        for i, doc in enumerate(docs):
            if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
                continue
            meta = doc.get("metadata") or {}
            if meta.get("name") != workload:
                continue
            if namespace and meta.get("namespace") not in (None, namespace):
                continue
            return path, i
    raise PatchError(f"Deployment {workload!r} not found under {root}")


def _container(doc: dict[str, Any], name: str | None) -> dict[str, Any]:
    containers = doc["spec"]["template"]["spec"]["containers"]
    if name:
        for c in containers:
            if c.get("name") == name:
                return c
    if len(containers) == 1 or not name:
        return containers[0]
    raise PatchError(f"container {name!r} not found")


def _increase_memory_limit(doc: dict[str, Any], params: dict[str, Any]) -> str:
    c = _container(doc, params.get("container"))
    resources = c.setdefault("resources", {})
    limits = resources.setdefault("limits", {})
    if "memory" not in limits:
        raise PatchError("container has no memory limit to raise")
    old = parse_quantity(limits["memory"])
    factor = float(params.get("factor", 1.5))
    new = math.ceil(old * factor)
    if "max" in params:
        cap = parse_quantity(params["max"])
        if old >= cap:
            raise PatchError(f"memory limit already at cap {params['max']}")
        new = min(new, cap)
    new_q = format_quantity(new)
    old_q = str(limits["memory"])
    limits["memory"] = new_q
    return f"container {c.get('name')}: memory limit {old_q} -> {new_q}"


def _rollback_image(doc: dict[str, Any], params: dict[str, Any]) -> str:
    image = params.get("image")
    if not image:
        raise PatchError("no previous image known to roll back to")
    c = _container(doc, params.get("container"))
    old = c.get("image")
    if old == image:
        raise PatchError("manifest already uses the previous image")
    c["image"] = image
    return f"container {c.get('name')}: image {old} -> {image}"


ACTIONS = {
    "increase_memory_limit": _increase_memory_limit,
    "rollback_image": _rollback_image,
}


def apply_action(repo_root: Path, manifests_dir: str, action: str, workload: str, namespace: str | None,
                 params: dict[str, Any], write: bool = False) -> PatchResult:
    """Apply ``action`` to the Deployment's manifest. ``path`` in the result is relative to ``repo_root``."""
    if action not in ACTIONS:
        raise PatchError(f"unknown action {action!r}")
    repo_root = Path(repo_root)
    path, index = find_deployment(repo_root / manifests_dir, workload, namespace)
    y = _yaml()
    original = path.read_text()
    docs = list(y.load_all(original))
    summary = ACTIONS[action](docs[index], params)
    buf = io.StringIO()
    y.dump_all(docs, buf)
    updated = buf.getvalue()
    if write:
        path.write_text(updated)
    return PatchResult(path=path.relative_to(repo_root), original=original, updated=updated, summary=summary)
