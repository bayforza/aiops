"""Incidents, received alerts and operator feedback, kept in one SQLite file.

SQLite keeps the community edition a single process with nothing to install. The schema is small on
purpose; the PostgreSQL + pgvector store for similar-incident search builds on the same records.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import Alert, Incident, utcnow

VOTES = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
STATUSES = ("open", "acknowledged", "resolved")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    fingerprint TEXT,
    decision TEXT NOT NULL,
    incident_id TEXT,
    labels TEXT NOT NULL,
    annotations TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author TEXT,
    root_cause TEXT,
    fix TEXT,
    comment TEXT
);
CREATE INDEX IF NOT EXISTS feedback_incident ON feedback (incident_id);
"""


class Store:
    def __init__(self, path: str | Path):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    def _insert(self, sql: str, args: tuple) -> int:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.lastrowid

    # incidents
    def save_incident(self, inc: Incident) -> None:
        self._exec("INSERT OR REPLACE INTO incidents (id, created_at, body) VALUES (?, ?, ?)",
                   (inc.id, inc.created_at.isoformat(), inc.model_dump_json()))

    def get_incident(self, incident_id: str) -> Incident | None:
        rows = self._exec("SELECT body FROM incidents WHERE id = ?", (incident_id,))
        return Incident.model_validate_json(rows[0]["body"]) if rows else None

    def incidents(self, limit: int = 500) -> list[Incident]:
        rows = self._exec("SELECT body FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,))
        return [Incident.model_validate_json(r["body"]) for r in rows]

    def set_status(self, incident_id: str, status: str) -> Incident | None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        inc = self.get_incident(incident_id)
        if inc is not None:
            inc.status = status
            self.save_incident(inc)
        return inc

    # alerts
    def record_alert(self, alert: Alert, decision: str, incident_id: str | None = None) -> int:
        return self._insert(
            "INSERT INTO alerts (received_at, name, status, fingerprint, decision, incident_id, labels, annotations)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (utcnow().isoformat(), alert.name, alert.status, alert.fingerprint, decision, incident_id,
             json.dumps(alert.labels), json.dumps(alert.annotations)))

    def link_alert(self, alert_id: int, incident_id: str, decision: str = "analysed") -> None:
        self._exec("UPDATE alerts SET incident_id = ?, decision = ? WHERE id = ?", (incident_id, decision, alert_id))

    def alerts(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(r), "labels": json.loads(r["labels"]), "annotations": json.loads(r["annotations"])}
                for r in rows]

    # feedback
    def add_feedback(self, incident_id: str, root_cause: str | None, fix: str | None, comment: str = "",
                     author: str | None = None) -> dict[str, Any]:
        for name, value in (("root_cause", root_cause), ("fix", fix)):
            if value is not None and value not in VOTES:
                raise ValueError(f"{name} must be one of {', '.join(VOTES)}")
        fid = self._insert(
            "INSERT INTO feedback (incident_id, created_at, author, root_cause, fix, comment)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (incident_id, utcnow().isoformat(), author, root_cause, fix, comment))
        return dict(self._exec("SELECT * FROM feedback WHERE id = ?", (fid,))[0])

    def feedback(self, incident_id: str | None = None) -> list[dict[str, Any]]:
        if incident_id:
            rows = self._exec("SELECT * FROM feedback WHERE incident_id = ? ORDER BY id", (incident_id,))
        else:
            rows = self._exec("SELECT * FROM feedback ORDER BY id")
        return [dict(r) for r in rows]

    def latest_votes(self) -> dict[str, dict[str, Any]]:
        """The most recent vote per incident; a later vote replaces an earlier one."""
        out: dict[str, dict[str, Any]] = {}
        for fb in self.feedback():
            cur = out.setdefault(fb["incident_id"], {"root_cause": None, "fix": None, "comment": "", "votes": 0})
            cur["votes"] += 1
            for k in ("root_cause", "fix"):
                if fb[k]:
                    cur[k] = fb[k]
            if fb["comment"]:
                cur["comment"] = fb["comment"]
        return out
