# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for security vulnerabilities. Report them through GitHub
**Security → Report a vulnerability** (private vulnerability reporting). We aim to respond within 3 business days.

## Scope and design guarantees

- The copilot runs **read-only** by default; it never executes commands itself and never merges PRs.
- The cloud second opinion is **off by default**. When enabled, only an anonymised summary is sent:
  values such as passwords, tokens, JWTs and private keys are removed; namespaces, workloads, pods, hosts,
  IPs and e-mail addresses are replaced with reversible placeholders (`NAMESPACE_1`, ...). The full text
  that was sent is written to the audit log and to the incident record (`second_opinion.sent_payload`).
- Incidents marked with `local_only_namespaces` or the `data_classification` label never leave the
  organisation under any condition.
- Every step is written to the hash-chained `audit.jsonl`; verify it with `aiops-copilot audit-verify`.
- API keys go in environment variables, not in the configuration file.

Anonymisation is regex-based and is not a 100% guarantee. In regulated environments (e.g. banking data
residency rules), review samples of `sent_payload` with your own data before enabling the cloud feature.
