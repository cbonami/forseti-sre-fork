#!/usr/bin/env bash
# Show the state of the production-like environment: pods, exposed services,
# scrape targets, active alerts, sink payload counts, node resource use.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

info "Pods ($NS_OBS)"
k -n "$NS_OBS" get pods -o wide
if k get ns "$NS_DEMO" >/dev/null 2>&1; then
  info "Pods ($NS_DEMO)"
  k -n "$NS_DEMO" get pods
fi

info "Host-exposed services (LoadBalancer -> Windows localhost)"
k -n "$NS_OBS" get svc -l app.kubernetes.io/part-of=forseti-test-infra \
  -o custom-columns='NAME:.metadata.name,TYPE:.spec.type,PORTS:.spec.ports[*].port' | grep -E 'NAME|LoadBalancer'

info "Prometheus scrape targets"
svc_get "$NS_OBS" lgtm:9090 /api/v1/targets \
  | jq -r '.data.activeTargets[] | "  \(.health)\t\(.labels.job)\t\(.scrapeUrl)\t\(.lastError)"' | sort

info "Prometheus alerts (state counts)"
svc_get "$NS_OBS" lgtm:9090 /api/v1/alerts \
  | jq -r '.data.alerts[] | "\(.state)\t\(.labels.alertname)\tscom=\(.labels.scom // "-")"' | sort | uniq -c

info "Alertmanager: active alerts by receiver"
svc_get "$NS_OBS" alertmanager:9093 /api/v2/alerts \
  | jq -r '.[] | "\(.receivers|map(.name)|join(","))\t\(.labels.alertname)"' | sort | uniq -c

info "Webhook sinks: payloads received (in memory since pod start)"
for s in sink-forseti sink-scom-legacy; do
  n="$(svc_get "$NS_OBS" "$s:8080" /payloads | jq 'length')"
  echo "  $s: $n"
done

info "Node resources"
k top node || true
echo
k top pods -A --sort-by=memory 2>/dev/null | head -15 || true
