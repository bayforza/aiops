"""Wire a Pipeline from Settings."""

from __future__ import annotations

import logging

from .actions import GitOpsActor
from .audit import AuditLog
from .collectors import GitChangesCollector, KubernetesCollector, LokiCollector, PrometheusCollector
from .config import Settings
from .llm import LLMClient
from .notify import Notifier
from .pipeline import Pipeline
from .runbooks import RunbookStore

log = logging.getLogger("aiops.factory")


def build_pipeline(s: Settings) -> Pipeline:
    collectors = []
    if s.kubernetes.enabled:
        try:
            collectors.append(KubernetesCollector(kubeconfig=s.kubernetes.kubeconfig, context=s.kubernetes.context))
        except Exception as exc:  # noqa: BLE001
            log.warning("kubernetes collector disabled: %s", exc)
    if s.prometheus.enabled:
        collectors.append(PrometheusCollector(s.prometheus.url))
    if s.loki.enabled:
        collectors.append(LokiCollector(s.loki.url, max_lines=s.context.max_log_lines))
    collectors.append(GitChangesCollector(s.gitops.repo_path, s.gitops.manifests_dir))

    llm = None
    if s.llm.enabled:
        llm = LLMClient(s.llm.base_url, s.llm.model, s.llm.api_key, s.llm.timeout_s,
                        s.llm.temperature, s.llm.max_tokens)
    return Pipeline(
        settings=s,
        collectors=collectors,
        runbooks=RunbookStore.from_dir(s.runbooks_dir),
        llm=llm,
        actor=GitOpsActor(s.gitops, s.output_dir),
        notifier=Notifier(s.notify.webhook_url),
        audit=AuditLog(s.audit_path),
    )
