"""Render the collected context as compact text within a character budget."""

from __future__ import annotations

from ..models import Alert, IncidentContext, Signal, Target


def _section(title: str, lines: list[str]) -> str:
    return f"## {title}\n" + "\n".join(lines) if lines else ""


def render_context(alert: Alert, target: Target, ctx: IncidentContext, signals: list[Signal],
                   budget_chars: int = 6000) -> str:
    # Sections in priority order; later ones are trimmed first when over budget.
    sections: list[tuple[str, list[str]]] = [
        ("Alert", [
            f"name={alert.name} severity={alert.labels.get('severity', '?')}",
            f"namespace={target.namespace} workload={target.workload} pod={target.pod}",
            f"summary={alert.annotations.get('summary') or alert.annotations.get('description', '')}"[:400],
        ]),
        ("Detected signals", [f"- [{s.kind} w={s.weight}] {s.detail}" for s in signals]),
        ("Containers", [
            f"- {c.name} image={c.image} ready={c.ready} restarts={c.restart_count} "
            f"waiting={c.waiting_reason} last_terminated={c.last_terminated_reason} "
            f"exit={c.last_exit_code} mem_limit={c.memory_limit}"
            for c in ctx.containers
        ]),
        ("Recent changes", [f"- {c.at.isoformat() if c.at else '?'} {c.description}" for c in ctx.changes]),
        ("Kubernetes events", [f"- {e}" for e in ctx.events[-15:]]),
        ("Metrics (last/min/max)", [
            f"- {m.name}: last={m.last:.3g} min={m.min:.3g} max={m.max:.3g} {m.unit}"
            for m in ctx.metrics if m.last is not None
        ]),
        ("Log templates (count, error?, sample)", [
            f"- x{t.count}{' ERROR' if t.is_error else ''}: {t.sample}" for t in ctx.log_templates
        ]),
    ]
    out: list[str] = []
    used = 0
    for title, lines in sections:
        kept: list[str] = []
        for line in lines:
            if used + len(line) + 1 > budget_chars:
                break
            kept.append(line)
            used += len(line) + 1
        block = _section(title, kept)
        if block:
            out.append(block)
    return "\n\n".join(out)
