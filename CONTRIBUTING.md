# Contributing

AIOps Copilot is an open-source project under the Apache-2.0 license (Community Edition). Contributions are welcome.

## Getting started

```bash
make setup     # .venv + dependencies
make test      # pytest; LLM, cloud and GitHub are mocked, no cluster needed
make lint      # ruff
make eval-rules
```

## Most valuable contributions

1. **A new incident scenario.** Add an anonymised real incident to `eval/fixtures.yaml`
   (alert + Kubernetes state + log lines + expected category). Product quality is measured with this set.
2. **A runbook.** An approved fix under `runbooks/`. The model only picks from these; it never invents a fix.
3. **A collector.** A new data source (Elasticsearch, Graylog, Tempo, ...). Implement the interface in
   `collectors/base.py`; collectors must be **read-only**.

## Rules

- Every change comes with tests; `make test` and `make lint` must pass.
- Prefer rule-based signals over the model: if a definitive signal can be found in code, don't put it in the prompt.
- Any path that sends data outside the organisation must be **off by default**, go through `anonymize.py`
  and be written to the audit log.
- Do not submit fixtures containing real customer data, credentials or internal hostnames.
- Keep commit messages short and descriptive; in the PR description, say what you tested and how.
- Write code, comments, docs and messages in English.

## License

Contributions you submit are licensed under the Apache License 2.0 (section 5 of the license).
