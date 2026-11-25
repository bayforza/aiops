from __future__ import annotations

from aiops_copilot.anonymize import Anonymizer
from aiops_copilot.models import Alert, Target

TARGET = Target(namespace="payments", workload="checkout-api", pod="checkout-api-7d9f8-abcde", container="app")
ALERT = Alert(labels={"alertname": "KubePodCrashLooping", "node": "worker-07", "instance": "10.20.30.40:9100"})

TEXT = """pod checkout-api-7d9f8-abcde in namespace payments (deployment checkout-api) on node worker-07
connect to db-primary.payments.svc.cluster.local:5432 from 10.20.30.41 failed
user ops@bank.com.tr password=Sup3rS3cret! Authorization: Bearer abc.def-ghi
token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl request 123e4567-e89b-12d3-a456-426614174000
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA
-----END RSA PRIVATE KEY-----
"""


def test_mask_removes_secrets_and_identifiers():
    anon = Anonymizer.for_incident(TARGET, ALERT)
    out = anon.mask(TEXT)
    for leaked in ("checkout-api", "payments", "worker-07", "10.20.30.41", "ops@bank.com.tr", "Sup3rS3cret",
                   "abc.def-ghi", "eyJhbGci", "MIIEowIBAAKCAQEA", "123e4567", "db-primary"):
        assert leaked not in out, leaked
    assert "POD_1" in out and "WORKLOAD_1" in out and "NAMESPACE_1" in out and "NODE_1" in out
    assert out.count("<REDACTED>") >= 4
    assert anon.secrets_removed >= 4


def test_same_value_same_placeholder_and_roundtrip():
    anon = Anonymizer.for_incident(TARGET, ALERT)
    out = anon.mask("checkout-api restarted; checkout-api again; 10.0.0.1 and 10.0.0.1")
    assert out.count("WORKLOAD_1") == 2
    assert anon.mask("checkout-api-6b5d4-xyz12 too") == "WORKLOAD_1-6b5d4-xyz12 too"  # sibling pod
    assert out.count("IP_1") == 2 and "IP_2" not in out
    answer = {"root_cause": "WORKLOAD_1 hits its memory limit on NODE_1", "evidence": ["IP_1 refused"]}
    back = anon.unmask_obj(answer)
    assert back == {"root_cause": "checkout-api hits its memory limit on worker-07", "evidence": ["10.0.0.1 refused"]}


def test_pod_replaced_before_workload_prefix():
    anon = Anonymizer.for_incident(TARGET)
    out = anon.mask("checkout-api-7d9f8-abcde")
    assert out == "POD_1"
    assert anon.unmask(out) == "checkout-api-7d9f8-abcde"


def test_extra_terms_and_word_boundaries():
    anon = Anonymizer.for_incident(Target(namespace="app"), extra={"Acme Bank": "CUSTOMER"})
    out = anon.mask("Acme Bank outage; application logs fine; ns app")
    assert "CUSTOMER_1" in out and "Acme" not in out
    assert "application" in out  # "app" inside a longer word is left alone
    assert out.endswith("NAMESPACE_1")
