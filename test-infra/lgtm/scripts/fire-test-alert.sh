#!/usr/bin/env bash
# POST a synthetic alert straight to Alertmanager (/api/v2/alerts), bypassing
# Prometheus. Useful to test receivers/routing without waiting for a rule.
#
#   fire-test-alert.sh [--name N] [--severity S] [--service S] [--scom]
#                      [--label k=v ...] [--resolve] [--minutes M]
#
#   --scom      add scom="true" (routes to scom-legacy AND forseti)
#   --resolve   send the same alert with endsAt=now (resolves it)
#   --minutes   validity (endsAt) for a firing alert, default 10
#
# The alert identity is its label set: re-run with the same flags plus
# --resolve to resolve it.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

name="ForsetiTestAlert"; severity="warning"; service="test-service"; scom=0
resolve=0; minutes=10; extra=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) name="$2"; shift 2 ;;
    --severity) severity="$2"; shift 2 ;;
    --service) service="$2"; shift 2 ;;
    --scom) scom=1; shift ;;
    --label) extra+=("$2"); shift 2 ;;
    --resolve) resolve=1; shift ;;
    --minutes) minutes="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ $resolve == 1 ]]; then
  starts="$(date -u -d '-1 minute' +%Y-%m-%dT%H:%M:%SZ)"; ends="$now"
else
  starts="$now"; ends="$(date -u -d "+${minutes} minutes" +%Y-%m-%dT%H:%M:%SZ)"
fi

labels="$(jq -n --arg a "$name" --arg sev "$severity" --arg svc "$service" \
  '{alertname:$a, severity:$sev, service:$svc, namespace:"observability", cluster:"local-k3s", env:"dev", source:"fire-test-alert"}')"
[[ $scom == 1 ]] && labels="$(jq '. + {scom:"true"}' <<<"$labels")"
for kv in "${extra[@]}"; do
  labels="$(jq --arg k "${kv%%=*}" --arg v "${kv#*=}" '. + {($k):$v}' <<<"$labels")"
done

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
jq -n --argjson l "$labels" --arg s "$starts" --arg e "$ends" '[{
  labels: $l,
  annotations: {
    summary: "Synthetic test alert \($l.alertname) for \($l.service)",
    description: "Posted by test-infra/lgtm/scripts/fire-test-alert.sh",
    runbook_url: "https://runbooks.local/test/fire-test-alert"
  },
  startsAt: $s, endsAt: $e,
  generatorURL: "http://localhost:19093/#/alerts"
}]' > "$tmp"

info "$( [[ $resolve == 1 ]] && echo Resolving || echo Firing ) $(jq -c . <<<"$labels")"
am_post /api/v2/alerts "$tmp" >/dev/null
echo "Accepted by Alertmanager. Payloads arrive after group_wait (10s):"
echo "  kubectl --context $KUBE_CONTEXT -n $NS_OBS logs deploy/sink-forseti --tail=5"
[[ $scom == 1 ]] && echo "  kubectl --context $KUBE_CONTEXT -n $NS_OBS logs deploy/sink-scom-legacy --tail=5"
exit 0
