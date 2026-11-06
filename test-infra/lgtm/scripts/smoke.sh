#!/usr/bin/env bash
# Smoke test: every backend answers and has data. Exit code = number of failures.
# Queries go through the API server service proxy, so this works from any
# shell with kubectl access (no host port forwarding needed).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

fails=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; fails=$((fails + 1)); }
warn() { printf '  WARN  %s\n' "$*"; }

promq() { # <service:port> <promql> -> result json array
  svc_get "$NS_OBS" "$1" "/api/v1/query?query=$(urlencode "$2")" | jq -c '.data.result'
}
num() { jq -r 'if length == 0 then "0" else .[0].value[1] end'; }

demo=0
k get ns "$NS_DEMO" >/dev/null 2>&1 && demo=1
now="$(date +%s)"; start=$((now - 900))

info "Grafana"
[[ "$(svc_get "$NS_OBS" lgtm:3000 /api/health | jq -r .database)" == "ok" ]] \
  && pass "/api/health database=ok" || fail "Grafana /api/health"

info "Prometheus"
n="$(promq lgtm:9090 'count(up == 1)' | num)"
t="$(promq lgtm:9090 'count(up)' | num)"
[[ "$n" == "$t" && "$n" -gt 0 ]] && pass "all $t scrape targets up" || fail "targets up: $n of $t"
for m in kube_pod_info container_cpu_usage_seconds_total synthetic_queue_depth; do
  c="$(promq lgtm:9090 "count($m)" | num)"
  [[ "$c" -gt 0 ]] && pass "$m: $c series" || fail "$m: no series"
done
if [[ $demo == 1 ]]; then
  for m in traces_span_metrics_calls_total traces_spanmetrics_calls_total; do
    c="$(promq lgtm:9090 "count($m)" | num)"
    [[ "$c" -gt 0 ]] && pass "$m: $c series" || fail "$m: no series"
  done
fi
r="$(svc_get "$NS_OBS" lgtm:9090 /api/v1/rules | jq '[.data.groups[].rules[]] | length')"
[[ "$r" -gt 0 ]] && pass "$r rules loaded" || fail "no rules loaded"

info "Thanos Query (same series as Prometheus)"
stores="$(svc_get "$NS_OBS" thanos-query:10902 /api/v1/stores | jq '[.data[][]? | select(.lastError == null)] | length')"
[[ "$stores" -ge 1 ]] && pass "$stores healthy store(s)" || fail "no healthy Thanos stores"
q='sort(synthetic_login_attempts_total)'
p="$(promq lgtm:9090 "$q" | jq -c '[.[].metric | del(.cluster, .env)] | sort_by(.user_id)')"
th="$(promq thanos-query:10902 "$q" | jq -c '[.[].metric | del(.cluster, .env)] | sort_by(.user_id)')"
ext="$(promq thanos-query:10902 "$q" | jq -r '.[0].metric | "\(.cluster)/\(.env)"')"
if [[ "$p" == "$th" && "$p" != "[]" ]]; then
  pass "$(jq length <<<"$p") identical series via Prometheus and Thanos (Thanos adds external labels $ext)"
else
  fail "series differ between Prometheus and Thanos for $q"
fi

info "Alertmanager"
[[ "$(svc_get "$NS_OBS" alertmanager:9093 /api/v2/status | jq -r '.config.original' | grep -c 'scom-legacy')" -gt 0 ]] \
  && pass "config loaded (scom-legacy route present)" || fail "Alertmanager status/config"
a="$(svc_get "$NS_OBS" alertmanager:9093 /api/v2/alerts | jq length)"
[[ "$a" -gt 0 ]] && pass "$a active alert(s)" || warn "no active alerts yet (rules need a few minutes)"
for s in sink-forseti sink-scom-legacy; do
  c="$(svc_get "$NS_OBS" "$s:8080" /payloads | jq length)"
  [[ "$c" -gt 0 ]] && pass "$s received $c payload(s)" || warn "$s has no payloads yet"
done

if [[ $demo == 1 ]]; then
  info "Loki (demo logs)"
  lq='sum by (service_name) (count_over_time({k8s_namespace_name="otel-demo"}[10m]))'
  res="$(svc_get "$NS_OBS" lgtm:3100 "/loki/api/v1/query?query=$(urlencode "$lq")")"
  ns="$(jq '.data.result | length' <<<"$res")"
  [[ "$ns" -gt 0 ]] && pass "logs from $ns demo services: $(jq -r '[.data.result[].metric.service_name] | sort | join(",")' <<<"$res")" \
    || fail "no demo logs in Loki"
  tq='sum(count_over_time({k8s_namespace_name="otel-demo"} | trace_id != "" [10m]))'
  c="$(svc_get "$NS_OBS" lgtm:3100 "/loki/api/v1/query?query=$(urlencode "$tq")" | jq -r '.data.result[0].value[1] // "0"')"
  [[ "$c" -gt 0 ]] && pass "$c log lines with trace_id (structured metadata) in 10m" || fail "no log lines with trace_id"

  info "Tempo (TraceQL)"
  tq='{ resource.service.name = "frontend" }'
  res="$(svc_get "$NS_OBS" lgtm:3200 "/api/search?q=$(urlencode "$tq")&start=$start&end=$now&limit=5")"
  c="$(jq '.traces | length' <<<"$res")"
  [[ "$c" -gt 0 ]] && pass "TraceQL $tq -> $c trace(s), e.g. $(jq -r '.traces[0].traceID' <<<"$res")" || fail "no traces for $tq"
else
  warn "namespace $NS_DEMO absent: skipping Loki/Tempo/span-metrics checks"
fi

echo
[[ $fails == 0 ]] && echo "SMOKE OK" || echo "SMOKE FAILED ($fails)"
exit "$fails"
