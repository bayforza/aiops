"""Recent commits in the GitOps repository: 'what changed before the alert?'."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta

from ..models import Alert, Change, IncidentContext, Target
from .base import Window


class GitChangesCollector:
    name = "git"

    def __init__(self, repo_path: str, manifests_dir: str, lookback: timedelta = timedelta(hours=6)):
        self.repo_path = repo_path
        self.manifests_dir = manifests_dir
        self.lookback = lookback

    async def collect(self, alert: Alert, target: Target, window: Window, ctx: IncidentContext) -> None:
        since = (window.end - self.lookback).isoformat()
        path = self.manifests_dir
        if target.workload:
            path = f"{self.manifests_dir}/{target.workload}"
        cmd = ["git", "-C", self.repo_path, "log", f"--since={since}", "--format=%H|%cI|%s", "--", path]
        try:
            out = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=10, check=True
            )
        except (subprocess.SubprocessError, OSError) as exc:
            ctx.errors.append(f"git: {exc}")
            return
        for line in out.stdout.splitlines()[:10]:
            sha, ts, subject = (line.split("|", 2) + ["", ""])[:3]
            ctx.changes.append(
                Change(at=datetime.fromisoformat(ts) if ts else None, source="git",
                       revision=sha[:10], description=f"commit {sha[:10]}: {subject}")
            )
