"""Send the analysis to a chat webhook (Slack-compatible {"text": ...} payload)."""

from __future__ import annotations

import logging

import httpx

from .models import ActionRecord, Incident

log = logging.getLogger("aiops.notify")


def render_message(incident: Incident) -> str:
    a = incident.analysis
    lines = [
        f"*{incident.alert.name}* · {incident.target.namespace}/{incident.target.workload or incident.target.pod}",
        f"Kategori: {a.category.value} · Güven: {a.confidence:.0%} · Kaynak: {a.source}",
        f"Kök neden: {a.root_cause}",
    ]
    lines += [f"• {e}" for e in a.evidence[:4]]
    if a.remediation.kind != "none":
        lines.append(f"Öneri ({a.remediation.kind}): {a.remediation.description}")
    for act in incident.actions:
        if act.url:
            lines.append(f"PR: {act.url}")
    return "\n".join(lines)


class Notifier:
    def __init__(self, webhook_url: str | None, client: httpx.AsyncClient | None = None):
        self.webhook_url = webhook_url
        self.client = client or httpx.AsyncClient(timeout=10)

    async def send(self, incident: Incident) -> ActionRecord:
        text = render_message(incident)
        log.info("incident %s\n%s", incident.id, text)
        if not self.webhook_url:
            return ActionRecord(kind="notify", status="logged", detail="no webhook configured")
        try:
            resp = await self.client.post(self.webhook_url, json={"text": text})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return ActionRecord(kind="notify", status="failed", detail=str(exc))
        return ActionRecord(kind="notify", status="sent")
