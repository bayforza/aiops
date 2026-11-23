"""Kubernetes collector: pod state, events and rollout history. Read-only API calls only."""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import Alert, Change, ContainerState, IncidentContext, Target
from .base import Window, workload_from_pod


def _load_apis(kubeconfig: str | None, context: str | None) -> tuple[Any, Any]:
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(config_file=kubeconfig, context=context)
    return client.CoreV1Api(), client.AppsV1Api()


def _owner(obj: Any, kind: str) -> str | None:
    for ref in obj.metadata.owner_references or []:
        if ref.kind == kind:
            return ref.name
    return None


class KubernetesCollector:
    name = "kubernetes"

    def __init__(self, core: Any = None, apps: Any = None, kubeconfig: str | None = None,
                 context: str | None = None):
        if core is None or apps is None:
            core, apps = _load_apis(kubeconfig, context)
        self.core, self.apps = core, apps

    async def collect(self, alert: Alert, target: Target, window: Window, ctx: IncidentContext) -> None:
        await asyncio.to_thread(self._collect, target, window, ctx)

    # The kubernetes client is synchronous, so the real work runs in a thread.
    def _collect(self, target: Target, window: Window, ctx: IncidentContext) -> None:
        ns = target.namespace
        if not ns:
            return
        try:
            self._resolve_workload(target)
            pods = self._pods(target)
            for pod in pods:
                ctx.containers.extend(self._container_states(pod))
            names = {p.metadata.name for p in pods} | ({target.pod} if target.pod else set())
            ctx.events.extend(self._events(ns, names, target.workload))
            if target.workload:
                ctx.changes.extend(self._rollouts(ns, target.workload, window))
        except Exception as exc:  # noqa: BLE001 - one failing source must not stop the analysis
            ctx.errors.append(f"kubernetes: {exc}")

    def _resolve_workload(self, target: Target) -> None:
        if target.workload or not target.pod:
            return
        try:
            pod = self.core.read_namespaced_pod(target.pod, target.namespace)
            rs_name = _owner(pod, "ReplicaSet")
            if rs_name:
                rs = self.apps.read_namespaced_replica_set(rs_name, target.namespace)
                target.workload = _owner(rs, "Deployment")
        except Exception:  # noqa: BLE001 - pod may already be gone
            pass
        target.workload = target.workload or workload_from_pod(target.pod)

    def _pods(self, target: Target) -> list[Any]:
        if target.workload:
            try:
                dep = self.apps.read_namespaced_deployment(target.workload, target.namespace)
                labels = dep.spec.selector.match_labels or {}
                selector = ",".join(f"{k}={v}" for k, v in labels.items())
                return list(self.core.list_namespaced_pod(target.namespace, label_selector=selector).items)
            except Exception:  # noqa: BLE001
                pass
        if target.pod:
            try:
                return [self.core.read_namespaced_pod(target.pod, target.namespace)]
            except Exception:  # noqa: BLE001
                return []
        return []

    @staticmethod
    def _container_states(pod: Any) -> list[ContainerState]:
        limits = {}
        for c in pod.spec.containers or []:
            res = c.resources.limits if c.resources and c.resources.limits else {}
            limits[c.name] = res.get("memory")
        states = []
        for cs in pod.status.container_statuses or []:
            waiting = cs.state.waiting if cs.state else None
            last = cs.last_state.terminated if cs.last_state else None
            states.append(
                ContainerState(
                    name=cs.name,
                    image=cs.image,
                    ready=bool(cs.ready),
                    restart_count=cs.restart_count or 0,
                    waiting_reason=waiting.reason if waiting else None,
                    waiting_message=(waiting.message or "")[:300] if waiting else None,
                    last_terminated_reason=last.reason if last else None,
                    last_exit_code=last.exit_code if last else None,
                    memory_limit=limits.get(cs.name),
                )
            )
        return states

    def _events(self, ns: str, names: set[str], workload: str | None) -> list[str]:
        events = self.core.list_namespaced_event(ns).items
        wanted = set(names)
        if workload:
            wanted.add(workload)
        out = []
        for ev in events:
            obj = ev.involved_object.name if ev.involved_object else ""
            if obj in wanted or (workload and obj.startswith(f"{workload}-")):
                count = f" (x{ev.count})" if ev.count and ev.count > 1 else ""
                out.append(f"{ev.type} {ev.reason} {ev.involved_object.kind}/{obj}: {ev.message}{count}")
        return out[-40:]

    def _rollouts(self, ns: str, workload: str, window: Window) -> list[Change]:
        changes = []
        for rs in self.apps.list_namespaced_replica_set(ns).items:
            if _owner(rs, "Deployment") != workload:
                continue
            ann = rs.metadata.annotations or {}
            images = ",".join(c.image for c in rs.spec.template.spec.containers)
            changes.append(
                Change(
                    at=rs.metadata.creation_timestamp,
                    source="k8s-rollout",
                    revision=ann.get("deployment.kubernetes.io/revision"),
                    image=images,
                    description=f"ReplicaSet {rs.metadata.name} revision "
                    f"{ann.get('deployment.kubernetes.io/revision', '?')} image={images}",
                )
            )
        changes.sort(key=lambda c: int(c.revision or 0))
        return changes[-5:]
