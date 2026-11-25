"""Mask sensitive values before an incident summary leaves the organisation.

Secrets are removed for good. Identifiers (namespaces, workloads, pods, hosts, IPs, e-mail
addresses) are replaced with stable placeholders such as NAMESPACE_1 and mapped back in the
answer, so the operator reads real names while the cloud provider never sees them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Alert, Target

_SECRET_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|credential)s?"
    r"(\s*[:=]\s*|\s+)([^\s,;\"']+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)

# (placeholder prefix, pattern) for reversible identifiers, applied in this order.
_IDENTIFIERS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("ID", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("HOST", re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:svc|local|internal|corp|lan|com|net|org|io|"
                        r"tr|cloud|cluster)\b(?::\d+)?", re.I)),
]


@dataclass
class Anonymizer:
    """Stateful masker for one incident: the same value always gets the same placeholder."""

    terms: dict[str, str] = field(default_factory=dict)  # real value -> placeholder (known identifiers)
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> real value
    secrets_removed: int = 0
    _counters: dict[str, int] = field(default_factory=dict)

    @classmethod
    def for_incident(cls, target: Target, alert: Alert | None = None,
                     extra: dict[str, str] | None = None) -> Anonymizer:
        anon = cls()
        # Longest first, so a pod name is replaced before the workload prefix inside it.
        known = [("POD", target.pod), ("WORKLOAD", target.workload), ("NAMESPACE", target.namespace),
                 ("CONTAINER", target.container)]
        if alert:
            for key in ("instance", "node", "service", "job"):
                known.append((key.upper(), alert.labels.get(key)))
        for prefix, value in sorted(known, key=lambda kv: -len(kv[1] or "")):
            if value and len(value) >= 3 and value not in anon.terms:
                anon.terms[value] = anon._placeholder(prefix, value)
        for value, prefix in (extra or {}).items():
            anon.terms.setdefault(value, anon._placeholder(prefix, value))
        return anon

    def _placeholder(self, prefix: str, value: str) -> str:
        for ph, real in self.mapping.items():
            if real == value:
                return ph
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        ph = f"{prefix}_{n}"
        self.mapping[ph] = value
        return ph

    def _drop(self, _: re.Match) -> str:
        self.secrets_removed += 1
        return "<REDACTED>"

    def _drop_value(self, m: re.Match) -> str:
        self.secrets_removed += 1
        return f"{m.group(1)}{m.group(2)}<REDACTED>"

    def mask(self, text: str) -> str:
        for rx in (_PRIVATE_KEY, _JWT, _BEARER):
            text = rx.sub(self._drop, text)
        text = _SECRET_KV.sub(self._drop_value, text)
        # Whole hosts, IPs and e-mails first: replacing a namespace inside db.payments.svc first would
        # break the host pattern and leave "db" in the clear.
        for prefix, rx in _IDENTIFIERS:
            text = rx.sub(lambda m, p=prefix: self._placeholder(p, m.group(0)), text)
        for value in sorted(self.terms, key=len, reverse=True):
            # A trailing hyphen is allowed so other pods of the same workload (name-<hash>) are masked too.
            text = re.sub(rf"(?<![\w-]){re.escape(value)}(?!\w)", self.terms[value], text)
        return text

    def unmask(self, text: str) -> str:
        for ph in sorted(self.mapping, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(ph)}\b", self.mapping[ph], text)
        return text

    def unmask_obj(self, obj):
        if isinstance(obj, str):
            return self.unmask(obj)
        if isinstance(obj, list):
            return [self.unmask_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask_obj(v) for k, v in obj.items()}
        return obj

    @property
    def replacements(self) -> int:
        return len(self.mapping)
