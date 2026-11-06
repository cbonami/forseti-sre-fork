#!/usr/bin/env bash
# Empirically determine which URLs reach the backends from
#   1. the Windows host            (curl.exe through WSL interop)
#   2. a docker compose container  (user-defined bridge network, like compose)
#   3. a pod -> a port published by a docker container on the VM
#      (what Alertmanager needs to call the config-service webhook in compose)
# Prints OK/-- per candidate URL. Needs docker (Rancher Desktop WSL integration).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
guard_context
command -v docker >/dev/null || die "docker not found"

CURL_IMG="curlimages/curl:8.17.0"
PY_IMG="python:3.13.9-slim"
NET="forseti-netprobe"
PROBE_PORT=18099

# Public images only: use an empty docker client config so a Windows
# credential helper configured in ~/.docker/config.json is not invoked.
DOCKER_CONFIG="$(mktemp -d)"; export DOCKER_CONFIG
echo '{}' > "$DOCKER_CONFIG/config.json"
cleanup() {
  docker rm -f forseti-netprobe-web >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  k -n "$NS_OBS" delete pod netprobe --ignore-not-found --wait=false >/dev/null 2>&1 || true
  rm -rf "$DOCKER_CONFIG"
}
trap cleanup EXIT

node_ip="$(k get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"

# name|path  (port is filled per candidate set)
backends=(
  "grafana|/api/health"
  "loki|/ready"
  "tempo|/ready"
  "prometheus|/-/ready"
  "thanos-query|/-/ready"
  "alertmanager|/-/ready"
)
host_port() { case "$1" in grafana) echo 13000;; loki) echo 13100;; tempo) echo 13200;;
  prometheus) echo 19090;; thanos-query) echo 19091;; alertmanager) echo 19093;; esac; }
svc_url() { case "$1" in
  grafana) echo "http://lgtm.observability.svc.cluster.local:3000";;
  loki) echo "http://lgtm.observability.svc.cluster.local:3100";;
  tempo) echo "http://lgtm.observability.svc.cluster.local:3200";;
  prometheus) echo "http://lgtm.observability.svc.cluster.local:9090";;
  thanos-query) echo "http://thanos-query.observability.svc.cluster.local:10902";;
  alertmanager) echo "http://alertmanager.observability.svc.cluster.local:9093";; esac; }

info "1. Windows host (curl.exe)"
if command -v curl.exe >/dev/null; then
  for b in "${backends[@]}"; do
    n="${b%%|*}"; p="${b#*|}"; u="http://localhost:$(host_port "$n")$p"
    code="$(curl.exe -s -m 5 -o NUL -w '%{http_code}' "$u" 2>/dev/null || true)"
    printf '  %-3s %-13s %s\n' "$([[ $code == 200 ]] && echo OK || echo "--")" "$n" "$u"
  done
else
  echo "  curl.exe not available through WSL interop; skipping"
fi

info "2. docker compose-style container (network $NET)"
docker network create "$NET" >/dev/null 2>&1 || true
script=""
for b in "${backends[@]}"; do
  n="${b%%|*}"; p="${b#*|}"; hp="$(host_port "$n")"
  for base in "http://$node_ip:$hp" "http://host.docker.internal:$hp" "$(svc_url "$n")"; do
    script+="c=\$(curl -s -m 4 -o /dev/null -w '%{http_code}' '$base$p'); [ \"\$c\" = 200 ] && s=OK || s=--; printf '  %-3s %-13s %s\n' \$s $n '$base';"
  done
done
script+="c=\$(curl -sk -m 4 -o /dev/null -w '%{http_code}' https://$node_ip:6443/version); printf '  %-3s %-13s %s (401 = reachable, TLS ok with -k)\n' \$c kube-api https://$node_ip:6443;"
script+="c=\$(curl -sk -m 4 -o /dev/null -w '%{http_code}' https://host.docker.internal:6443/version); printf '  %-3s %-13s %s\n' \$c kube-api https://host.docker.internal:6443;"
docker run --rm --network "$NET" --entrypoint sh "$CURL_IMG" -c "$script"

info "3. pod -> port $PROBE_PORT published by a docker container on the VM"
docker rm -f forseti-netprobe-web >/dev/null 2>&1 || true
docker run -d --name forseti-netprobe-web --network "$NET" -p "$PROBE_PORT:8000" "$PY_IMG" \
  python3 -m http.server 8000 >/dev/null
sleep 2
cands=("http://$node_ip:$PROBE_PORT/" "http://host.docker.internal:$PROBE_PORT/" \
       "http://host.rancher-desktop.internal:$PROBE_PORT/" "http://gateway.rancher-desktop.internal:$PROBE_PORT/")
pscript=""
for u in "${cands[@]}"; do
  pscript+="c=\$(curl -s -m 4 -o /dev/null -w '%{http_code}' '$u'); [ \"\$c\" = 200 ] && s=OK || s=--; printf '  %-3s %s\n' \$s '$u';"
done
k -n "$NS_OBS" run netprobe --image="$CURL_IMG" --restart=Never --command -- sh -c "$pscript" >/dev/null
k -n "$NS_OBS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/netprobe --timeout=90s >/dev/null || true
k -n "$NS_OBS" logs netprobe
