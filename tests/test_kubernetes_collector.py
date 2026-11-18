"""KubernetesCollector against a fake API that mimics the kubernetes client's objects."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from aiops_copilot.collectors import KubernetesCollector, Window
from aiops_copilot.models import Alert, IncidentContext, Target
from aiops_copilot.preprocess import detect_signals

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)


def meta(name, owner=None, kind=None, ann=None, created=None):
    refs = [NS(kind=kind, name=owner)] if owner else []
    return NS(name=name, owner_references=refs, annotations=ann or {}, creation_timestamp=created)


POD = NS(
    metadata=meta("payment-gateway-7b9d6c5f4d-m3n8t", "payment-gateway-7b9d6c5f4d", "ReplicaSet"),
    spec=NS(containers=[NS(name="app", resources=NS(limits={"memory": "32Mi"}))]),
    status=NS(container_statuses=[NS(
        name="app", image="busybox:1.37", ready=False, restart_count=5,
        state=NS(waiting=NS(reason="CrashLoopBackOff", message="back-off 2m40s")),
        last_state=NS(terminated=NS(reason="Error", exit_code=2)))]),
)


def rs(name, rev, image, created):
    return NS(metadata=meta(name, "payment-gateway", "Deployment",
                            {"deployment.kubernetes.io/revision": rev}, created),
              spec=NS(template=NS(spec=NS(containers=[NS(image=image)]))))


class Core:
    def read_namespaced_pod(self, name, ns):
        return POD

    def list_namespaced_pod(self, ns, label_selector=None):
        assert label_selector == "app=payment-gateway"
        return NS(items=[POD])

    def list_namespaced_event(self, ns):
        return NS(items=[
            NS(type="Warning", reason="BackOff", count=6, message="Back-off restarting failed container",
               involved_object=NS(kind="Pod", name=POD.metadata.name)),
            NS(type="Normal", reason="Pulled", count=1, message="unrelated",
               involved_object=NS(kind="Pod", name="other-pod")),
        ])


class Apps:
    def read_namespaced_replica_set(self, name, ns):
        return rs(name, "3", "busybox:1.37", NOW)

    def read_namespaced_deployment(self, name, ns):
        return NS(spec=NS(selector=NS(match_labels={"app": "payment-gateway"})))

    def list_namespaced_replica_set(self, ns):
        return NS(items=[
            rs("payment-gateway-6c8b7d9f5e", "2", "busybox:1.36", NOW - timedelta(days=1)),
            rs("payment-gateway-7b9d6c5f4d", "3", "busybox:1.37", NOW - timedelta(minutes=5)),
        ])


def test_collects_state_events_and_rollouts():
    target = Target(namespace="demo", pod=POD.metadata.name)
    ctx = IncidentContext()
    collector = KubernetesCollector(core=Core(), apps=Apps())
    asyncio.run(collector.collect(Alert(), target, Window(NOW - timedelta(minutes=30), NOW), ctx))
    assert ctx.errors == []
    assert target.workload == "payment-gateway"
    assert ctx.containers[0].waiting_reason == "CrashLoopBackOff"
    assert ctx.containers[0].memory_limit == "32Mi"
    assert len(ctx.events) == 1 and "(x6)" in ctx.events[0]
    assert [c.revision for c in ctx.changes] == ["2", "3"]
    kinds = {s.kind for s in detect_signals(ctx, NOW)}
    assert {"crash_loop", "recent_rollout"} <= kinds
