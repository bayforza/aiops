from __future__ import annotations

from pathlib import Path

import pytest

from aiops_copilot.config import Settings
from aiops_copilot.runbooks import RunbookStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def runbooks() -> RunbookStore:
    return RunbookStore.from_dir(ROOT / "runbooks")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.output_dir = str(tmp_path / "out")
    s.audit_path = str(tmp_path / "out" / "audit.jsonl")
    s.runbooks_dir = str(ROOT / "runbooks")
    s.gitops.repo_path = str(ROOT)
    return s
