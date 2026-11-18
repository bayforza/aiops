from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aiops_copilot.actions.patches import PatchError, apply_action, format_quantity, parse_quantity


@pytest.fixture
def repo(tmp_path: Path, root: Path) -> Path:
    shutil.copytree(root / "gitops", tmp_path / "gitops")
    return tmp_path


def test_quantities():
    assert parse_quantity("64Mi") == 64 * 2**20
    assert parse_quantity("1Gi") == 2**30
    assert parse_quantity("500M") == 500 * 10**6
    assert format_quantity(96 * 2**20) == "96Mi"
    assert format_quantity(2 * 2**30) == "2Gi"


def test_increase_memory_limit_keeps_comments(repo: Path):
    res = apply_action(repo, "gitops/apps", "increase_memory_limit", "checkout-api", "demo",
                       {"factor": 1.5, "max": "4Gi", "container": "app"}, write=True)
    assert "64Mi -> 96Mi" in res.summary
    text = (repo / res.path).read_text()
    assert "memory: 96Mi" in text
    assert "# Scenario \"oom\"" in text  # comment survived
    changed = [ln for ln in res.diff.splitlines() if ln.startswith(("+ ", "- ", "+\t", "-\t"))
               or (ln[:1] in "+-" and not ln.startswith(("+++", "---")))]
    assert len(changed) == 2  # one line removed, one added


def test_memory_cap(repo: Path):
    with pytest.raises(PatchError):
        apply_action(repo, "gitops/apps", "increase_memory_limit", "checkout-api", "demo", {"max": "64Mi"})


def test_rollback_image(repo: Path):
    res = apply_action(repo, "gitops/apps", "rollback_image", "inventory-svc", "demo",
                       {"image": "nginx:1.26-alpine"})
    assert "nginx:1.27-alpine -> nginx:1.26-alpine" in res.summary
    assert "+          image: nginx:1.26-alpine" in res.diff


def test_unknown_workload(repo: Path):
    with pytest.raises(PatchError):
        apply_action(repo, "gitops/apps", "rollback_image", "nope", "demo", {"image": "x"})
