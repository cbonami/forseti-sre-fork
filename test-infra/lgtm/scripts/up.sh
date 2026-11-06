#!/usr/bin/env bash
# Install / update the fake-production monitored environment (idempotent).
#   test-infra/lgtm/scripts/up.sh            # everything
#   test-infra/lgtm/scripts/up.sh --no-demo  # skip the OpenTelemetry Demo
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

WITH_DEMO=1
for a in "$@"; do
  case "$a" in
    --no-demo) WITH_DEMO=0 ;;
    -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

command -v helm >/dev/null || die "helm not found"
command -v jq >/dev/null || die "jq not found"

info "Helm repos"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community open-telemetry >/dev/null

info "Observability stack (namespace $NS_OBS)"
k apply -k "$LGTM_DIR/k8s/observability"

info "kube-state-metrics $KSM_CHART_VERSION"
h upgrade --install kube-state-metrics prometheus-community/kube-state-metrics \
  --version "$KSM_CHART_VERSION" -n "$NS_OBS" \
  -f "$LGTM_DIR/helm/kube-state-metrics-values.yaml" >/dev/null

if [[ $WITH_DEMO == 1 ]]; then
  info "OpenTelemetry Demo $DEMO_CHART_VERSION (namespace $NS_DEMO)"
  h upgrade --install otel-demo open-telemetry/opentelemetry-demo \
    --version "$DEMO_CHART_VERSION" -n "$NS_DEMO" --create-namespace \
    -f "$LGTM_DIR/helm/otel-demo-values.yaml" >/dev/null
fi

info "Waiting for observability rollouts"
for d in lgtm thanos-query alertmanager sink-forseti sink-scom-legacy synthetic-exporter kube-state-metrics; do
  k -n "$NS_OBS" rollout status "deploy/$d" --timeout=10m
done

if [[ $WITH_DEMO == 1 ]]; then
  info "Waiting for demo rollouts (first run pulls ~20 images)"
  for d in $(k -n "$NS_DEMO" get deploy -o name); do
    k -n "$NS_DEMO" rollout status "$d" --timeout=15m
  done
  k -n "$NS_DEMO" rollout status ds/otel-collector-agent --timeout=5m
fi

# Old hash-suffixed ConfigMaps from earlier applies are left behind by
# `kubectl apply -k`; remove the ones no pod references any more.
info "Pruning unreferenced generated ConfigMaps"
in_use="$(k -n "$NS_OBS" get pods -o json | jq -r '.items[].spec.volumes[]?.configMap.name // empty' | sort -u)"
for cm in $(k -n "$NS_OBS" get cm -l app.kubernetes.io/part-of=forseti-test-infra -o name | sed 's#configmap/##'); do
  case "$cm" in
    prometheus-config-*|alertmanager-config-*|thanos-endpoints-*|synthetic-scripts-*)
      grep -qx "$cm" <<<"$in_use" || k -n "$NS_OBS" delete cm "$cm" >/dev/null ;;
  esac
done

cat <<EOF

Done. From the Windows host:
  Grafana       http://localhost:13000   (admin / admin)
  Prometheus    http://localhost:19090
  Thanos Query  http://localhost:19091
  Alertmanager  http://localhost:19093
  Loki          http://localhost:13100
  Tempo         http://localhost:13200
Next: $LGTM_DIR/scripts/status.sh, smoke.sh, gen-agent-kubeconfig.sh
EOF
