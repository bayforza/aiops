"""Wire a Pipeline from Settings."""

from __future__ import annotations

import logging
import os

from .actions import GitOpsActor
from .audit import AuditLog
from .collectors import GitChangesCollector, KubernetesCollector, LokiCollector, PrometheusCollector
from .config import CloudConfig, LLMConfig, Settings
from .llm import ClaudeClient, LLMClient
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

    llms = [_local_client(c) for c in [s.llm, *s.local_llms] if c.enabled]
    return Pipeline(
        settings=s,
        collectors=collectors,
        runbooks=RunbookStore.from_dir(s.runbooks_dir),
        llms=llms,
        actor=GitOpsActor(s.gitops, s.output_dir),
        notifier=Notifier(s.notify.webhook_url),
        audit=AuditLog(s.audit_path),
        cloud_llm=build_cloud_client(s.cloud, s.llm),
    )


def _local_client(c: LLMConfig) -> LLMClient:
    return LLMClient(c.base_url, c.model, c.api_key, c.timeout_s, c.temperature, c.max_tokens, name=c.name)


def build_cloud_client(cloud: CloudConfig, llm: LLMConfig):
    """The cloud second-opinion client, or None unless enabled and its API key env var is set."""
    key = os.environ.get(cloud.api_key_env)
    if not cloud.enabled or not key:
        return None
    if cloud.provider == "anthropic":
        client = ClaudeClient(cloud.model, key, cloud.timeout_s, effort=cloud.effort, name=cloud.name, kind="cloud")
    else:
        client = LLMClient(cloud.base_url, cloud.model, key, cloud.timeout_s, llm.temperature, llm.max_tokens,
                           name=cloud.name, kind="cloud")
    log.warning("cloud second opinion enabled: anonymised summaries may be sent to %s (%s)",
                cloud.provider, cloud.model)
    return client
