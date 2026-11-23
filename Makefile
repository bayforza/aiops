SHELL := /bin/bash
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
CONFIG ?= config.yaml

.PHONY: setup cluster monitoring up run eval eval-rules test lint down scenario-%

setup:            ## install tools, pull the model, create venv (macOS)
	./scripts/setup-mac.sh

cluster:          ## create the kind cluster
	kind create cluster --config deploy/kind/kind-config.yaml

monitoring:       ## Prometheus + Alertmanager + Grafana + Loki + Alloy
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
	helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
	helm repo update >/dev/null
	helm upgrade --install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
	  -f deploy/monitoring/kube-prometheus-values.yaml --wait --timeout 10m
	helm upgrade --install loki grafana/loki -n monitoring -f deploy/monitoring/loki-values.yaml --wait --timeout 10m
	helm upgrade --install alloy grafana/alloy -n monitoring -f deploy/monitoring/alloy-values.yaml --wait
	kubectl apply -f deploy/monitoring/extras.yaml

up: cluster monitoring

run:              ## start the copilot webhook receiver on :8080
	$(PY) -m aiops_copilot.cli -c $(CONFIG) serve --port 8080

scenario-%:       ## make scenario-oom | scenario-bad-deploy | scenario-image-pull | scenario-config-error | scenario-reset
	./scripts/scenario.sh $*

eval:             ## RCA accuracy + latency of the local model on recorded incidents
	$(PY) -m eval.run_eval -c $(CONFIG)

eval-rules:       ## same, rules only (no LLM): the baseline the model must beat
	$(PY) -m eval.run_eval -c $(CONFIG) --no-llm

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests eval

down:
	kind delete cluster --name aiops
