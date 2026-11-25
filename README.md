# AIOps Copilot: Community Edition

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

A **read-only** incident copilot that runs inside your organisation. It receives an alert from
Alertmanager, gathers context from Kubernetes, Prometheus, Loki and Git, finds the root cause with a
**local LLM**, and proposes the fix as a **pull request** to your GitOps repository. It never executes
anything itself and never merges.

```
Alertmanager ──webhook──▶ Copilot ──▶ Kubernetes / Prometheus / Loki / Git   (read-only)
                              │
                              ├─ Drain3 log templates + rule-based signals (CPU, milliseconds)
                              ├─ Local LLM (Ollama / llama.cpp / vLLM, OpenAI-compatible API)
                              ├─ Evidence score → if uncertain, cloud second opinion on an anonymised summary (optional, off by default)
                              ├─ Runbook selection  →  GitOps patch  →  PR (a human approves)
                              └─ Hash-chained audit log  +  Slack/Teams notification
```

## Design principles

- **Rules first, then the model.** Definitive signals such as OOMKilled, ImagePullBackOff and
  CreateContainerConfigError are found by rules. The model summarises them and reads from the logs what
  the rules miss (e.g. a YAML error, a DNS failure). If the model contradicts a definitive rule,
  confidence is lowered and no PR is opened.
- **Small context.** 500 log lines collapse to 5-20 templates with Drain3; the prompt is ~1,500 tokens.
  This is what makes CPU-only inference practical.
- **The model does not invent fixes.** It only picks one of the approved runbooks under `runbooks/`;
  the patch itself is produced by code (`increase_memory_limit`, `rollback_image`).
- **Graded autonomy.** `autonomy_level: 0` only notifies and writes a diff preview. `1` opens a PR when
  the confidence threshold is met. Direct execution (level 2+) is deliberately absent.
- **Auditability.** Every step is written, hash-chained, to `out/audit.jsonl`; check its integrity with
  `aiops-copilot audit-verify`.

## Hybrid AI: local first, cloud only when needed

The model's self-reported confidence is not trusted on its own. `confidence.py` computes a separate
**evidence score** from what was actually observed: is there a definitive signal for the category, do
independent sources (Kubernetes, metrics, logs) point at the same cause, do the rules and the model agree.

`routing.py` decides whether a second opinion is allowed and needed (in this order):

| Condition | Route |
|---|---|
| `cloud.enabled: false` (default) | local |
| namespace is in `local_only_namespaces` | local only |
| alert has `data_classification=restricted/confidential` | local only |
| API key env var not set | local |
| `min(confidence, evidence score) >= min_local_confidence` | local |
| otherwise | cloud, with an anonymised summary |

Text sent to the cloud goes through `anonymize.py`: secrets are removed, names and addresses are replaced
with placeholders such as `WORKLOAD_1` and `HOST_1`, and the answer is shown to the operator with the
real names. If the cloud answer is not better supported by the evidence, the local result is kept; even
when it is adopted, its confidence cannot exceed its evidence score, so no PR is opened on the cloud's
word alone. The full text that was sent is kept in the audit log and in `second_opinion.sent_payload`.
Details: [SECURITY.md](SECURITY.md).

```bash
export AIOPS_CLOUD_API_KEY=...        # the key lives in the environment, not in the config file
AIOPS_CLOUD_ENABLED=true .venv/bin/python -m eval.run_eval --cloud   # cloud_route_rate, cloud_agreed
```

## Local test environment (macOS on Apple Silicon, 16 GB)

Give Docker Desktop ~6 GB of RAM. Ollama runs outside the cluster, on the Mac, using Metal.

```bash
make setup          # kind, kubectl, helm, ollama + model (qwen2.5:7b-instruct) + .venv
make up             # kind cluster + Prometheus/Alertmanager/Grafana + Loki + Alloy (~5-10 min)
make run            # copilot listens on :8080 (keep it running in a separate terminal)

make scenario-oom           # checkout-api OOMKilled           → proposes a memory limit increase
make scenario-bad-deploy    # payment-gateway crashes on a new image → proposes rollback to the previous image
make scenario-image-pull    # inventory-svc wrong image tag     → proposes rollback
make scenario-config-error  # notification-svc missing ConfigMap → manual recommendation
make scenario-reset
```

The alert arrives within 1-2 minutes. Results:

- `out/incidents/<id>.json`: the full analysis (signals, evidence, root cause, timings)
- `out/<id>.patch` and `out/<id>-pr.md`: the proposed diff and PR text (dry run)
- `http://localhost:8080/incidents`: API
- Grafana `http://localhost:3000` (admin/admin, local test cluster only), Prometheus `:9090`, Alertmanager `:9093`

**To open real PRs:** set `gitops.dry_run: false` in `config.yaml` and `export GITHUB_TOKEN=...`
(a fine-grained token with `contents:write` + `pull_requests:write` on this repository only).
The copilot uses a temporary git worktree and never touches your working directory. The scenario scripts
make their "deploy" inside `gitops/apps`, so in PR mode commit and push that change first.

> Alertmanager reaches the copilot via `host.docker.internal:8080` (Docker Desktop). If it does not, check
> `kubectl -n monitoring logs alertmanager-kps-kube-prometheus-stack-alertmanager-0`.

## Model evaluation (eval)

`eval/fixtures.yaml` contains 12 recorded incidents:

```bash
make eval-rules     # rules only: 10/12 (2 cases are deliberately unsolvable by rules)
make eval           # rules + local model: accuracy and time per case
```

Results are written to `out/eval/results-<model>.json`. Compare different models
(`AIOPS_LLM_MODEL=...`) and a CPU-only llama.cpp server on the same table. Adding fixtures from your own
past incidents is the most convincing part of a proof of concept.

## Development

```bash
make test           # pytest (LLM, cloud and GitHub are mocked; no cluster needed)
make lint
.venv/bin/aiops-copilot -c config.yaml analyze alert.json   # analyse a single alert
```

The model writes `root_cause` in the language set by `llm.language` (default `en`).

## Layout

| Path | Contents |
|---|---|
| `src/aiops_copilot/collectors/` | Kubernetes, Prometheus, Loki, Git (read-only) |
| `src/aiops_copilot/preprocess/` | Drain3 log summary, rule-based signals, context budget |
| `src/aiops_copilot/llm.py` | OpenAI-compatible local model client and prompt |
| `src/aiops_copilot/pipeline.py` | Flow, model + rule merge, policy gate |
| `src/aiops_copilot/confidence.py` | Evidence-based confidence score |
| `src/aiops_copilot/routing.py` | Local / cloud routing decision |
| `src/aiops_copilot/anonymize.py` | Secret removal, reversible masking |
| `src/aiops_copilot/actions/` | GitOps patches and opening PRs |
| `runbooks/` | Approved fixes (the model only picks from these) |
| `gitops/apps/` | Demo applications and failure scenarios |
| `deploy/` | kind and monitoring stack values |
| `eval/` | Recorded incidents and accuracy/latency measurement |

## Roadmap

| Week | Scope | Status |
|---|---|---|
| 1 | Local AI engine: collectors, Drain3, rules, local LLM, runbooks, dry-run PR, audit, eval | Done |
| 2 | Hybrid AI: evidence score, local/cloud routing, anonymisation, cloud usage rate | This release |
| 3 | Feedback and knowledge base: PostgreSQL + pgvector, incident UI (React), correct/partial/wrong rating, RAG over past incidents, interactive Slack/Teams buttons | Next |
| 4 | Remediation: real GitHub PRs, ArgoCD tracking and post-deploy verification, Terraform plan + approval, Ansible/AWX, Jira/ServiceNow tickets | Planned |
| ★ | Tempo traces, Elasticsearch/Graylog, Helm chart + RBAC + Dockerfile + CI, 20+ scenarios, demo video | Planned |

## Contributing and license

Apache License 2.0. See [CONTRIBUTING.md](CONTRIBUTING.md) for contributions and [SECURITY.md](SECURITY.md)
for reporting vulnerabilities. Enterprise installation, integration and support are offered separately.
