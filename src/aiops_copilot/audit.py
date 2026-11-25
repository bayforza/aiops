"""Append-only, hash-chained audit log.

Every record carries the hash of the previous one, so editing or deleting a
line breaks the chain and `verify()` reports where.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from .models import utcnow

GENESIS = "0" * 64


def _digest(prev: str, body: dict[str, Any]) -> str:
    payload = prev + json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = GENESIS
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def append(self, event: str, incident_id: str | None = None, **data: Any) -> dict[str, Any]:
        with self._lock:
            prev = self._last_hash()
            body = {"ts": utcnow().isoformat(), "event": event, "incident_id": incident_id, "data": data}
            record = {**body, "prev": prev, "hash": _digest(prev, body)}
            with self.path.open("a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
            return record

    def events(self, incident_id: str) -> list[dict[str, Any]]:
        """All records of one incident, oldest first (for the incident timeline)."""
        if not self.path.exists():
            return []
        out = []
        with self.path.open() as fh:
            for line in fh:
                if incident_id in line:
                    rec = json.loads(line)
                    if rec.get("incident_id") == incident_id:
                        out.append({k: rec[k] for k in ("ts", "event", "data")})
        return out

    def verify(self) -> tuple[bool, str]:
        if not self.path.exists():
            return True, "audit log is empty"
        prev = GENESIS
        with self.path.open() as fh:
            for n, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                rec = json.loads(line)
                body = {k: rec[k] for k in ("ts", "event", "incident_id", "data")}
                if rec["prev"] != prev or rec["hash"] != _digest(prev, body):
                    return False, f"chain broken at line {n}"
                prev = rec["hash"]
        return True, "audit chain intact"
