#!/usr/bin/env bash
# Remove the production-like environment (demo, kube-state-metrics, observability
# stack incl. its PVC, the agent RBAC). Data in Prometheus/Loki/Tempo is lost.
#   test-infra/lgtm/scripts/down.sh            # everything
#   test-infra/lgtm/scripts/down.sh --demo-only
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

DEMO_ONLY=0
[[ "${1:-}" == "--demo-only" ]] && DEMO_ONLY=1

info "Uninstalling OpenTelemetry Demo"
h uninstall otel-demo -n "$NS_DEMO" --wait 2>/dev/null || true
k delete namespace "$NS_DEMO" --ignore-not-found --wait=false
[[ $DEMO_ONLY == 1 ]] && exit 0

info "Uninstalling kube-state-metrics"
h uninstall kube-state-metrics -n "$NS_OBS" --wait 2>/dev/null || true

info "Deleting observability stack"
k delete -k "$LGTM_DIR/k8s/observability" --ignore-not-found --wait=false
k delete namespace "$NS_OBS" --ignore-not-found --wait=true --timeout=3m || true
info "Done"
