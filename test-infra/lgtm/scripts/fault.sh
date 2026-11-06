#!/usr/bin/env bash
# Toggle an OpenTelemetry Demo fault flag (flagd) by patching the flagd-config
# ConfigMap and restarting flagd -- the same mechanism as
# scripts/e2e_test_otel_demo.py, adapted to chart 0.42.6 names
# (ConfigMap "flagd-config", Deployment "flagd").
#
#   fault.sh list
#   fault.sh <flagName> on [variant]   # variant default: "on", else "100%", else first non-off
#   fault.sh <flagName> off
#
# Examples: fault.sh paymentFailure on 50%   |   fault.sh adHighCpu on   |   fault.sh paymentFailure off
#
# Why a restart: the flagd pod copies the ConfigMap into an emptyDir at
# start-up (shared with the flagd-ui sidecar), so ConfigMap edits are not seen
# by a running flagd.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

CM=flagd-config
KEY=demo.flagd.json

usage() { sed -n '2,13p' "$0"; exit 1; }
[[ $# -ge 1 ]] || usage

cfg="$(k -n "$NS_DEMO" get cm "$CM" -o json | jq -r --arg k "$KEY" '.data[$k]')"
[[ -n "$cfg" && "$cfg" != "null" ]] || die "ConfigMap $NS_DEMO/$CM has no $KEY"

if [[ "$1" == "list" ]]; then
  jq -r '.flags | to_entries[] | "\(.key)\t\(.value.defaultVariant)\t[\(.value.variants|keys|join(" "))]"' <<<"$cfg" | column -t -s $'\t'
  exit 0
fi

flag="$1"; action="${2:-}"; variant="${3:-}"
[[ "$action" == "on" || "$action" == "off" ]] || usage
jq -e --arg f "$flag" '.flags[$f]' <<<"$cfg" >/dev/null \
  || die "flag '$flag' not found. Available: $(jq -r '.flags|keys|join(", ")' <<<"$cfg")"

if [[ "$action" == "off" ]]; then
  variant="off"
elif [[ -z "$variant" ]]; then
  variant="$(jq -r --arg f "$flag" '
      .flags[$f].variants | keys as $ks
      | if ($ks|index("on")) then "on"
        elif ($ks|index("100%")) then "100%"
        else ([$ks[] | select(. != "off")][0]) end' <<<"$cfg")"
fi
jq -e --arg f "$flag" --arg v "$variant" '.flags[$f].variants | has($v)' <<<"$cfg" >/dev/null \
  || die "variant '$variant' not valid for $flag: $(jq -r --arg f "$flag" '.flags[$f].variants|keys|join(", ")' <<<"$cfg")"

current="$(jq -r --arg f "$flag" '.flags[$f].defaultVariant' <<<"$cfg")"
info "$flag: $current -> $variant"

new_cfg="$(jq --arg f "$flag" --arg v "$variant" '.flags[$f].defaultVariant = $v' <<<"$cfg")"
patch="$(jq -n --arg k "$KEY" --arg v "$new_cfg" '{data: {($k): $v}}')"
k -n "$NS_DEMO" patch cm "$CM" --type=merge -p "$patch" >/dev/null

info "Restarting flagd"
k -n "$NS_DEMO" rollout restart deploy/flagd >/dev/null
k -n "$NS_DEMO" rollout status deploy/flagd --timeout=3m
info "Done. Flag state now: $(k -n "$NS_DEMO" get cm "$CM" -o json | jq -r --arg k "$KEY" --arg f "$flag" '.data[$k] | fromjson | .flags[$f].defaultVariant')"
