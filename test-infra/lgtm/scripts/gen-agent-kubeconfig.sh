#!/usr/bin/env bash
# Generate .local/kubeconfig-agent.yaml for the read-only ServiceAccount
# observability/forseti-agent-ro (context "local-k3s-ro").
#
# The server address must work from a docker compose container on the
# Rancher Desktop VM (where sre-agent runs). Measured (net-probe.sh):
#   https://<node InternalIP, 192.168.127.2>:6443  OK, in the k3s cert SANs  <- default
#   https://host.docker.internal:6443              OK (via Windows host forward), in SANs
#   https://127.0.0.1:6443                          NOT reachable from a container
#
#   gen-agent-kubeconfig.sh [--server URL] [--out FILE] [--test]
#   --test  verify from a throwaway container on a compose-style network
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context

server=""; out="$LOCAL_DIR/kubeconfig-agent.yaml"; test=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) server="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --test) test=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "$server" ]]; then
  ip="$(k get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"
  [[ -n "$ip" ]] || die "could not determine node InternalIP"
  server="https://$ip:6443"
fi

secret=forseti-agent-ro-token
for _ in $(seq 1 30); do
  token_b64="$(k -n "$NS_OBS" get secret "$secret" -o jsonpath='{.data.token}' 2>/dev/null || true)"
  [[ -n "$token_b64" ]] && break
  sleep 1
done
[[ -n "${token_b64:-}" ]] || die "secret $NS_OBS/$secret has no token (run up.sh first)"
ca_b64="$(k -n "$NS_OBS" get secret "$secret" -o jsonpath='{.data.ca\.crt}')"

mkdir -p "$(dirname "$out")"
umask 077
# Token never goes to stdout; it is decoded straight into the file.
{
  cat <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: local-k3s
    cluster:
      server: $server
      certificate-authority-data: $ca_b64
users:
  - name: forseti-agent-ro
    user:
      token: $(base64 -d <<<"$token_b64")
contexts:
  - name: local-k3s-ro
    context:
      cluster: local-k3s
      user: forseti-agent-ro
      namespace: otel-demo
current-context: local-k3s-ro
EOF
} > "$out"
chmod 600 "$out"
info "Wrote $out (server $server, context local-k3s-ro)"

# Permission sanity check (against the local API, from this shell).
chk() { kubectl --kubeconfig "$out" --server https://127.0.0.1:6443 auth can-i "$@" 2>/dev/null || true; }
echo "  can list pods:          $(chk list pods -A)"
echo "  can get pods/log:       $(chk get pods/log -n otel-demo)"
echo "  can list events:        $(chk list events -A)"
echo "  can get secrets:        $(chk get secrets -n otel-demo)   (must be no)"
echo "  can delete pods:        $(chk delete pods -n otel-demo)   (must be no)"
echo "  can create pods/exec:   $(chk create pods/exec -n otel-demo)   (must be no)"

if [[ $test == 1 ]]; then
  command -v docker >/dev/null || die "--test needs docker (WSL with Rancher Desktop integration)"
  info "Testing from a container on a compose-style bridge network"
  # Public image only: an empty client config avoids invoking a Windows
  # credential helper configured in ~/.docker/config.json.
  DOCKER_CONFIG="$(mktemp -d)"; export DOCKER_CONFIG
  trap 'rm -rf "$DOCKER_CONFIG"' EXIT
  echo '{}' > "$DOCKER_CONFIG/config.json"
  docker network create forseti-kubeconfig-test >/dev/null 2>&1 || true
  docker run --rm --network forseti-kubeconfig-test -v "$out:/kubeconfig:ro" \
    --entrypoint kubectl registry.k8s.io/kubectl:v1.34.13 \
    --kubeconfig /kubeconfig get pods -n otel-demo --request-timeout=10s | head -5
  docker network rm forseti-kubeconfig-test >/dev/null 2>&1 || true
fi
