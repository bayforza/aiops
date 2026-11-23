#!/usr/bin/env bash
# Trigger a fault scenario in the demo namespace.
# The "deploy" steps edit gitops/apps in your working tree, as a GitOps commit would.
set -euo pipefail
cd "$(dirname "$0")/.."

APPS=gitops/apps
sedi() { if sed --version >/dev/null 2>&1; then sed -i "$@"; else sed -i '' "$@"; fi; }

kubectl apply -f "$APPS/namespace.yaml" >/dev/null

case "${1:-}" in
  oom)
    kubectl apply -f "$APPS/checkout-api/"
    ;;
  bad-deploy)
    git checkout -- "$APPS/payment-gateway/deployment.yaml" 2>/dev/null || true
    kubectl apply -f "$APPS/payment-gateway/"
    kubectl -n demo rollout status deploy/payment-gateway --timeout=120s
    echo "deploying the broken release (busybox:1.37)"
    sedi 's#image: busybox:1.36#image: busybox:1.37#' "$APPS/payment-gateway/deployment.yaml"
    kubectl apply -f "$APPS/payment-gateway/"
    ;;
  image-pull)
    git checkout -- "$APPS/inventory-svc/deployment.yaml" 2>/dev/null || true
    kubectl apply -f "$APPS/inventory-svc/"
    kubectl -n demo rollout status deploy/inventory-svc --timeout=120s
    echo "deploying a mistyped image tag"
    sedi 's#image: nginx:1.27-alpine#image: nginx:1.27-alpne#' "$APPS/inventory-svc/deployment.yaml"
    kubectl apply -f "$APPS/inventory-svc/"
    ;;
  config-error)
    kubectl apply -f "$APPS/notification-svc/"
    ;;
  reset)
    git checkout -- "$APPS" 2>/dev/null || true
    kubectl delete namespace demo --ignore-not-found
    ;;
  *)
    echo "usage: $0 {oom|bad-deploy|image-pull|config-error|reset}"
    exit 1
    ;;
esac
echo "scenario '${1}' applied. Alert arrives in ~1-2 minutes: watch 'make run' output."
