#!/usr/bin/env bash
# shellcheck disable=SC2034  # variables are consumed by the sourcing scripts
# Shared helpers for test-infra/lgtm scripts. Source it; do not execute.
#
# Safety guard: every script refuses to run unless the kube context is
# "rancher-desktop" AND that context points at a local API server. The context
# is taken from $KUBE_CONTEXT if set, otherwise from the current context.
# All kubectl/helm calls then pass that context explicitly, so a different
# current-context (or a KUBECONFIG listing company clusters) is never used.

set -euo pipefail

LGTM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$LGTM_DIR/../.." && pwd)"
LOCAL_DIR="$REPO_ROOT/.local"

# Pinned versions (keep in sync with README.md).
KSM_CHART_VERSION="9.0.0"        # prometheus-community/kube-state-metrics (app v2.20.0)
DEMO_CHART_VERSION="0.42.6"      # open-telemetry/opentelemetry-demo (app 3.1.0)

NS_OBS="observability"
NS_DEMO="otel-demo"
REQUIRED_CONTEXT="rancher-desktop"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

guard_context() {
  command -v kubectl >/dev/null || die "kubectl not found (run from WSL Ubuntu)"
  local ctx="${KUBE_CONTEXT:-}"
  if [[ -z "$ctx" ]]; then
    ctx="$(kubectl config current-context 2>/dev/null || true)"
  fi
  if [[ "$ctx" != "$REQUIRED_CONTEXT" ]]; then
    die "refusing to run: kube context is '${ctx:-<none>}', expected '$REQUIRED_CONTEXT'.
       Set KUBE_CONTEXT=$REQUIRED_CONTEXT or switch the current context."
  fi
  local server
  server="$(kubectl config view -o jsonpath="{.clusters[?(@.name==\"$(kubectl config view -o jsonpath="{.contexts[?(@.name==\"$ctx\")].context.cluster}")\")].cluster.server}")"
  case "$server" in
    https://127.0.0.1:*|https://localhost:*) ;;
    *) die "refusing to run: context '$ctx' points at '$server', not a local API server." ;;
  esac
  KUBE_CONTEXT="$ctx"
  export KUBE_CONTEXT
}

k() { kubectl --context "$KUBE_CONTEXT" "$@"; }
h() { helm --kube-context "$KUBE_CONTEXT" "$@"; }

# GET through the API server's service proxy: works from anywhere kubectl
# works, independent of host port forwarding.
#   svc_get <namespace> <service>:<port> <path-with-query>
svc_get() {
  k get --raw "/api/v1/namespaces/$1/services/http:$2/proxy$3"
}

# POST a JSON file to Alertmanager's own API from inside its pod (busybox
# wget). `kubectl create --raw` is not usable here: Alertmanager answers it
# with 415 Unsupported Media Type.
#   am_post <path> <json-file>
am_post() {
  k -n "$NS_OBS" exec deploy/alertmanager -c alertmanager -- \
    wget -qO- --header "Content-Type: application/json" \
    --post-data "$(cat "$2")" "http://127.0.0.1:9093$1"
}

urlencode() {
  jq -rn --arg v "$1" '$v|@uri'
}
