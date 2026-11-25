"""Propose remediations as GitOps pull requests. The copilot never applies changes itself."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

from ..config import GitOpsConfig
from ..models import ActionRecord, Incident
from .patches import PatchError, PatchResult, apply_action


def render_pr_body(incident: Incident, patch: PatchResult) -> str:
    a = incident.analysis
    evidence = "\n".join(f"- {e}" for e in a.evidence) or "- (none)"
    return f"""## AIOps Copilot proposal

**Alert:** `{incident.alert.name}` · `{incident.target.namespace}/{incident.target.workload}`
**Category:** `{a.category.value}` · **Confidence:** {a.confidence:.0%} · **Source:** {a.source}
**Runbook:** `{a.runbook_id or '-'}`

### Root cause
{a.root_cause}

### Evidence
{evidence}

### Proposed change
{patch.summary}

> This PR was opened automatically and **must not be merged without human approval**.
> Incident ID: `{incident.id}` (audit log'da izlenebilir)
"""


def _git(cwd: str | Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()


class GitOpsActor:
    def __init__(self, cfg: GitOpsConfig, output_dir: str | Path, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.output_dir = Path(output_dir)
        self.client = client or httpx.AsyncClient(timeout=30)

    async def propose(self, incident: Incident, preview_only: bool = False) -> ActionRecord:
        """Open a PR, or with ``preview_only`` (or dry_run config) just write the diff to disk."""
        rem = incident.analysis.remediation
        if rem.kind != "gitops_patch" or not rem.action:
            return ActionRecord(kind="skipped", status="no_patch", detail="remediation is not a GitOps patch")
        if not incident.target.workload:
            return ActionRecord(kind="skipped", status="no_target", detail="workload unknown")
        if self.cfg.dry_run or preview_only:
            return await asyncio.to_thread(self._dry_run, incident)
        return await self._pull_request(incident)

    def _patch(self, root: Path, incident: Incident, write: bool) -> PatchResult:
        rem = incident.analysis.remediation
        return apply_action(root, self.cfg.manifests_dir, rem.action or "", incident.target.workload or "",
                            incident.target.namespace, rem.params, write=write)

    def _dry_run(self, incident: Incident) -> ActionRecord:
        try:
            patch = self._patch(Path(self.cfg.repo_path), incident, write=False)
        except PatchError as exc:
            return ActionRecord(kind="dry_run_patch", status="failed", detail=str(exc))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / f"{incident.id}.patch").write_text(patch.diff)
        (self.output_dir / f"{incident.id}-pr.md").write_text(render_pr_body(incident, patch))
        return ActionRecord(kind="dry_run_patch", status="written", detail=patch.summary, diff=patch.diff)

    async def _pull_request(self, incident: Incident) -> ActionRecord:
        token = os.environ.get(self.cfg.token_env)
        if not token:
            return ActionRecord(kind="pull_request", status="failed", detail=f"{self.cfg.token_env} not set")
        branch = f"aiops/{incident.id}"
        try:
            patch = await asyncio.to_thread(self._commit_on_worktree, incident, branch)
        except (PatchError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            return ActionRecord(kind="pull_request", status="failed", detail=str(detail)[:500])
        title = f"[aiops] {incident.target.workload}: {patch.summary}"[:120]
        resp = await self.client.post(
            f"https://api.github.com/repos/{self.cfg.github_repo}/pulls",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"title": title, "head": branch, "base": self.cfg.base_branch,
                  "body": render_pr_body(incident, patch)},
        )
        if resp.status_code >= 300:
            return ActionRecord(kind="pull_request", status="failed", detail=resp.text[:500], diff=patch.diff)
        return ActionRecord(kind="pull_request", status="opened", detail=patch.summary,
                            url=resp.json().get("html_url"), diff=patch.diff)

    def _commit_on_worktree(self, incident: Incident, branch: str) -> PatchResult:
        """Work in a throwaway worktree so the operator's checkout is never touched."""
        repo = Path(self.cfg.repo_path)
        remote, base = self.cfg.remote, self.cfg.base_branch
        _git(repo, "fetch", remote, base)
        with tempfile.TemporaryDirectory(prefix="aiops-") as tmp:
            wt = Path(tmp) / "wt"
            _git(repo, "worktree", "add", "-b", branch, str(wt), f"{remote}/{base}")
            try:
                patch = self._patch(wt, incident, write=True)
                _git(wt, "add", str(patch.path))
                _git(wt, "-c", "user.name=aiops-copilot", "-c", "user.email=aiops-copilot@localhost",
                     "commit", "-m", f"aiops: {patch.summary}\n\nIncident: {incident.id}")
                _git(wt, "push", remote, f"{branch}:{branch}")
                return patch
            finally:
                _git(repo, "worktree", "remove", "--force", str(wt))
