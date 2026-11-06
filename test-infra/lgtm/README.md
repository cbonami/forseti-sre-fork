# Local "production-like" monitored environment (k3s)

A local stand-in for a typical production monitoring stack, used to develop and test
Forseti (the OpenSRE fork) against realistic telemetry and alerts:

- **Metrics** in Prometheus, queried through **Thanos Query** (as in a typical production setup)
- **Logs** in Loki, **traces** in Tempo, Grafana on top (`grafana/otel-lgtm`)
- **Prometheus Alertmanager** routing like a typical production setup: `scom="true"` alerts go to
  the legacy SCOM bridge *and* to Forseti; everything else to Forseti
- A real workload (**OpenTelemetry Demo**) with fault-injection flags, plus a
  **synthetic noisy exporter** for alerts that should be tuned rather than
  investigated
- A **read-only ServiceAccount** and kubeconfig for the agent's Kubernetes skill

Everything runs on Rancher Desktop's single-node k3s (context
`rancher-desktop`). All scripts refuse to run against any other context.

```
                    Windows host (localhost:13000/13100/13200/19090/19091/19093)
                                     | Rancher Desktop forwards LoadBalancer ports
 ---------------------------------- k3s node 192.168.127.2 -----------------------------------
 ns otel-demo                         ns observability
 +----------------------+  OTLP/HTTP  +--------------------------------------------------+
 | demo services        | ----------> | pod lgtm: grafana/otel-lgtm 0.40.0               |
 | otel-collector (ds)  |   :4318     |   otelcol -> Prometheus(OTLP) / Tempo / Loki     |
 | flagd (fault flags)  |             |   Prometheus: rules, scrapes, external_labels    |
 +----------------------+             |   + thanos sidecar (StoreAPI, rule reloader)     |
                                      +----------------------+---------------------------+
 kube-state-metrics, cAdvisor ------> scrape                 | gRPC 10901
 synthetic-exporter (noisy) --------> scrape        thanos-query (PromQL HTTP :10902)
                                      Prometheus --alerts--> alertmanager :9093
                                                     scom="true" --> sink-scom-legacy (+continue)
                                                     everything  --> sink-forseti
```

## Versions (pinned)

| Component | Version | Source |
|---|---|---|
| grafana/otel-lgtm | `0.40.0` (Grafana v13.3.0, Prometheus v3.16.0, Tempo v3.1.1, Loki v3.7.9, OTel Collector v0.164.0) | Docker Hub |
| Thanos (sidecar + query) | `quay.io/thanos/thanos:v0.42.5` | quay.io / GitHub releases |
| Alertmanager | `prom/alertmanager:v0.34.2` | Docker Hub |
| kube-state-metrics chart | `prometheus-community/kube-state-metrics` `9.0.0` (app v2.20.0) | Helm repo |
| OpenTelemetry Demo chart | `open-telemetry/opentelemetry-demo` `0.42.6` (app 3.1.0) | Helm repo |
| Sinks / synthetic exporter | `python:3.13.9-slim` (stdlib only, script from ConfigMap) | Docker Hub |
| Probe images | `curlimages/curl:8.17.0`, `registry.k8s.io/kubectl:v1.34.13` | |

## Prerequisites

- Rancher Desktop with Kubernetes enabled (k3s, `dockerd (moby)` runtime). Tested
  on k3s v1.34.3, node `snowwhite`, 20 CPU / 20 GiB.
- WSL Ubuntu with `kubectl`, `helm` (3.20 works; the demo chart is apiVersion v2),
  `jq`, and the Rancher Desktop docker integration (only for `net-probe.sh` and
  `gen-agent-kubeconfig.sh --test`).
- Run every script **from WSL**:

```bash
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/cbona/github/forseti-sre && test-infra/lgtm/scripts/up.sh'
```

### Context guard

Each script sources `scripts/lib.sh`, which refuses to run unless the context
(`$KUBE_CONTEXT`, else the current context) is `rancher-desktop` **and** that
context's API server is `https://127.0.0.1:*` or `https://localhost:*`. Every
`kubectl`/`helm` call then passes `--context`/`--kube-context` explicitly.

## Scripts

| Script | What it does |
|---|---|
| `scripts/up.sh [--no-demo]` | Idempotent install/update: `kubectl apply -k k8s/observability`, kube-state-metrics, OTel Demo; waits for rollouts; prunes stale generated ConfigMaps. First run pulls ~25 images (~5 min). |
| `scripts/down.sh [--demo-only]` | Uninstalls the demo, KSM and the observability namespace (PVC included: data is lost). |
| `scripts/status.sh` | Pods, host-exposed services, scrape targets, alert states, Alertmanager receivers per alert, sink payload counts, `kubectl top`. |
| `scripts/smoke.sh` | Checks each backend has data (exit code = number of failures). See "Verification" below. |
| `scripts/fault.sh list` / `fault.sh <flag> on [variant]` / `fault.sh <flag> off` | Toggles an OTel Demo flagd flag by patching ConfigMap `otel-demo/flagd-config` and restarting `deploy/flagd` (same mechanism as `scripts/e2e_test_otel_demo.py`, adapted to chart 0.42.6 names). `on` picks variant `on`, else `100%`, else the first non-`off` variant. |
| `scripts/fire-test-alert.sh [--name N] [--severity S] [--service S] [--scom] [--label k=v]... [--resolve]` | POSTs a synthetic alert to Alertmanager `/api/v2/alerts` (from inside the Alertmanager pod). Re-run with `--resolve` to resolve it. |
| `scripts/gen-agent-kubeconfig.sh [--server URL] [--test]` | Writes `.local/kubeconfig-agent.yaml` (git-ignored, mode 600) for SA `observability/forseti-agent-ro`, context `local-k3s-ro`, and prints an RBAC check. `--test` runs `kubectl get pods` from a container on a compose-style network. |
| `scripts/net-probe.sh` | Re-measures the URL tables below empirically (Windows host via `curl.exe`, a compose-style container, and a pod calling a docker-published port). |

Queries in `status.sh`/`smoke.sh` go through the API server's service proxy
(`kubectl get --raw /api/v1/namespaces/.../services/http:svc:port/proxy/...`),
so they work from any shell with kubectl access.

## URLs

Measured with `scripts/net-probe.sh` (2026-11-06).

### 1. From the Windows host

| Backend | URL | Auth |
|---|---|---|
| Grafana | http://localhost:13000 | `admin` / `admin` (anonymous access **disabled**, unlike the image default) |
| Loki | http://localhost:13100 | none |
| Tempo | http://localhost:13200 | none |
| Prometheus | http://localhost:19090 | none |
| Thanos Query (PromQL HTTP API + UI) | http://localhost:19091 | none |
| Alertmanager | http://localhost:19093 | none |
| OTLP gRPC / HTTP into otel-lgtm | localhost:14317 / http://localhost:14318 | none |

These are `LoadBalancer` services (k3s ServiceLB binds the ports on the node,
Rancher Desktop forwards them to Windows localhost). None collide with
OpenSRE's 3002/8001/8081/5433/7475/7688/4001.

### 2. From a docker compose container on the Rancher VM (where OpenSRE runs)

| Backend | Recommended | Also works |
|---|---|---|
| Grafana | `http://192.168.127.2:13000` | `http://host.docker.internal:13000` |
| Loki | `http://192.168.127.2:13100` | `http://host.docker.internal:13100` |
| Tempo | `http://192.168.127.2:13200` | `http://host.docker.internal:13200` |
| Prometheus | `http://192.168.127.2:19090` | `http://host.docker.internal:19090` |
| Thanos Query | `http://192.168.127.2:19091` | `http://host.docker.internal:19091` |
| Alertmanager | `http://192.168.127.2:19093` | `http://host.docker.internal:19093` |
| Kubernetes API | `https://192.168.127.2:6443` | `https://host.docker.internal:6443` |

- `192.168.127.2` is the k3s node InternalIP (Rancher Desktop's VM address); it
  hits the ServiceLB ports directly. `host.docker.internal` resolves to
  `192.168.127.254` (the Windows host) and goes through Rancher Desktop's port
  forwarding, i.e. one more hop that a host firewall/VPN could break.
- `*.svc.cluster.local` names do **not** resolve in compose containers.
- `127.0.0.1:6443` (the server in the Windows kubeconfig) is not reachable from
  a container. Both recommended API addresses are in the k3s certificate SANs
  (`DNS:host.docker.internal`, `IP:192.168.127.2`, ...), so TLS verifies with the
  cluster CA; no `insecure-skip-tls-verify` needed.

### 3. From a pod (in-cluster)

| Backend | URL |
|---|---|
| Grafana | `http://lgtm.observability.svc.cluster.local:3000` |
| Loki | `http://lgtm.observability.svc.cluster.local:3100` |
| Tempo | `http://lgtm.observability.svc.cluster.local:3200` |
| Prometheus | `http://lgtm.observability.svc.cluster.local:9090` |
| Thanos Query | `http://thanos-query.observability.svc.cluster.local:10902` (gRPC 10901) |
| Alertmanager | `http://alertmanager.observability.svc.cluster.local:9093` |
| OTLP | `lgtm.observability.svc.cluster.local:4317` (gRPC) / `:4318` (HTTP) |

**Pod -> a port published by a docker container on the VM** (needed in wave 2
for Alertmanager -> config-service webhook in docker compose; tested with a
container publishing `18099`):

| URL | Result |
|---|---|
| `http://192.168.127.2:<published-port>` | OK (recommended) |
| `http://host.docker.internal:<published-port>` | OK (pods resolve it through CoreDNS -> node resolver) |
| `http://host.rancher-desktop.internal:<published-port>` | OK |
| `http://gateway.rancher-desktop.internal:<published-port>` | fails |

So in wave 2 the `forseti` receiver becomes e.g.
`http://192.168.127.2:8081/<intake path>` in `k8s/observability/config/alertmanager.yml`.

## Alerting

### Alertmanager routing (`k8s/observability/config/alertmanager.yml`)

```
route (receiver forseti, group_by [alertname, namespace, service],
       group_wait 10s, group_interval 30s, repeat_interval 15m)
 |- scom="true"  -> scom-legacy   continue: true
 |- (catch-all)  -> forseti
```

The catch-all must be an explicit child route: when a child matches, the
parent's receiver is not used, so with only the scom child the scom alerts
would never reach Forseti. Checked with `amtool config routes test`:
`scom=true` -> `scom-legacy,forseti`; otherwise `forseti`. Both webhooks have
`send_resolved: true`.

Receivers point at two echo sinks (`files/webhook_sink.py`, stdlib only). Each
POST is logged as one JSON line and kept in memory:

```bash
kubectl --context rancher-desktop -n observability logs deploy/sink-forseti | jq -c '.payload | {status, receiver, alerts: [.alerts[].labels.alertname]}'
kubectl --context rancher-desktop get --raw /api/v1/namespaces/observability/services/http:sink-scom-legacy:8080/proxy/payloads | jq length
```

### Rules (`k8s/observability/rules/`, ConfigMap `prometheus-rules`)

Rule changes are live: the ConfigMap keeps a stable name, the Thanos sidecar
watches the directory (`--reloader.rule-dir`, 30s) and POSTs `/-/reload`
(Prometheus runs with `--web.enable-lifecycle`). Run `up.sh` or
`kubectl apply -k` and wait ~1-2 min for kubelet to sync the volume.
Every alert has `severity`, `namespace`, `service` (where derivable) and
`summary`/`description`/`runbook_url` annotations; Prometheus adds the external
labels `cluster="local-k3s"`, `env="dev"`.

| Alert | Source | Severity | scom | Notes |
|---|---|---|---|---|
| `ServiceHighErrorRate` | demo span metrics (`traces_span_metrics_calls_total`) | critical | true | >5% server/consumer spans in error over 5m and >=3 errors, for 1m. `load-generator` excluded. |
| `ServiceHighLatencyP95` | demo span metrics (duration histogram) | warning | - | p95 > 1000 ms for 5m (baseline max ~170 ms). |
| `KubePodNotReady` | KSM | warning | true | Pending/Unknown/Failed, or Running but not ready, for 3m. |
| `KubePodRestartingFrequently` | KSM | warning | - | >= 2 restarts in 15m. |
| `KubeContainerCrashLooping` | KSM | critical | true | CrashLoopBackOff for 2m. |
| `PodCpuHigh` | cAdvisor (pod level) | warning | - | > 0.5 cores for 5m. |
| `PodMemoryGrowthToLimit` | cAdvisor + KSM limits | warning | true | 30m linear trend hits the pod memory limit within 1h, usage > 50%, pod older than 30m, for 10m. |
| `SyntheticLoginFailuresPerUser` | synthetic exporter | warning | true | **Noise:** one alert per `user_id` (20 users) during the exporter's 15-min "on" phase of every 30 min. |
| `SyntheticQueueDepthFlapping` | synthetic exporter | warning | - | **Noise:** square wave, period 4 min, `for: 30s` -> fires/resolves every ~4 min. |
| `SyntheticDiskUsageHigh` | synthetic exporter | critical | true | **Noise:** steady ~62% against a 50% threshold -> fires forever. |

`service` comes from the alert expression (`service_name` for span metrics) or
from the recording rule `namespace_pod:service:info`, built from KSM
`kube_pod_labels` (`app.kubernetes.io/component`, else `app.kubernetes.io/name`).

### Fault injection

```bash
test-infra/lgtm/scripts/fault.sh list
test-infra/lgtm/scripts/fault.sh paymentFailure on        # variant 100%
test-infra/lgtm/scripts/fault.sh paymentFailure on 25%
test-infra/lgtm/scripts/fault.sh paymentFailure off
```

Observed with `paymentFailure` at 100% (5 load-generator users): payment and
checkout errors show in span metrics after ~2.5 min, `ServiceHighErrorRate`
fires for `payment` and `checkout` after ~4 min and reaches both sinks
(scom="true"); after `off`, the resolved notifications arrive ~2.5 min later.
Useful flags: `paymentFailure`, `paymentUnreachable`,
`cartFailure`, `productCatalogFailure`, `adFailure` (errors); `adHighCpu`
(CPU); `emailMemoryLeak` (memory); `imageSlowLoad`, `recommendationCacheFailure`
(latency); `failedReadinessProbe` (pod not ready); `kafkaQueueProblems`.

## Logs <-> traces

The demo collector ships logs over OTLP to otel-lgtm's collector, which sends
them to Loki's native OTLP endpoint (`/otlp`). Observed result:

- **`trace_id` and `span_id` are Loki *structured metadata*.** They are not
  index labels and are not in the log line (the line is the plain message, e.g.
  `Payment request failed. Invalid token. ...`). Shown with
  `X-Loki-Response-Encoding-Flags: categorize-labels`.
- Index labels: `service_name`, `service_namespace`, `service_instance_id`,
  `k8s_namespace_name`, `k8s_pod_name`, `k8s_deployment_name`,
  `k8s_replicaset_name`, `k8s_container_name`, `k8s_daemonset_name`.
- Everything else (severity_text, scope_name, log attributes, host/process
  attributes) is structured metadata too.

```logql
{k8s_namespace_name="otel-demo", service_name="payment"} | trace_id != ""
{service_name="payment"} | trace_id="f1897d57df516ab69d6b29b6722e1010"
```

A `trace_id` taken from such a line resolves in Tempo
(`GET http://localhost:13200/api/v2/traces/<trace_id>`), e.g. a 48-span
checkout trace with the failing `oteldemo.PaymentService/Charge` span. TraceQL:
`GET /api/search?q={ resource.service.name = "payment" && status = error }&start=..&end=..`.

## Agent kubeconfig

```bash
test-infra/lgtm/scripts/gen-agent-kubeconfig.sh --test
# -> .local/kubeconfig-agent.yaml  (context local-k3s-ro, server https://192.168.127.2:6443)
```

- ServiceAccount `observability/forseti-agent-ro`, long-lived token from a
  `kubernetes.io/service-account-token` Secret (dev only).
- ClusterRole `forseti-agent-ro`: get/list/watch on pods, pods/log, events
  (core and events.k8s.io), services, endpoints(+slices), nodes, namespaces,
  configmaps, PVCs/PVs, RCs, quotas, limitranges, serviceaccounts, apps/*,
  batch/*, HPAs, PDBs, ingresses, networkpolicies, storageclasses; get/list on
  metrics.k8s.io. **No secrets, no exec/attach/port-forward, no writes**
  (checked by the script with `kubectl auth can-i`).
- The token is written straight into the file and never printed.

## Resource use

Measured with `kubectl top` with everything running: node total ~7.5-8 GiB
memory (including k3s system pods) and ~1.5 cores. Largest pods: `lgtm`
~1.6 GiB, `load-generator` ~0.6 GiB, `kafka` ~0.55 GiB. Well inside the
16 GiB budget. The `lgtm` PVC (local-path, 20Gi request, not enforced) holds
Prometheus (7d retention), Loki and Tempo data.

## Environment quirks found (and how they are handled)

1. **SDK `container.id` does not match the kubelet container ID** under Rancher
   Desktop's docker runtime (cri-dockerd), so the collector's `k8s_attributes`
   association by `container.id` silently failed and logs/metrics lost
   `k8s.namespace.name`, `k8s.pod.name`, ... `helm/otel-demo-values.yaml`
   reorders `pod_association` (pod IP, pod UID, connection, then container.id).
2. **cAdvisor only labels pod-level cgroups** (`namespace`, `pod`, no
   `container`) on this runtime. Resource rules use pod-level series
   (`pod!="", container=""`), which also exist on containerd clusters such as AKS.
3. **Helm `null` does not delete subchart keys** (helm 3.20 renders `key: null`):
   the demo collector keeps its unused jaeger/prometheus/opensearch/firepit
   exporters (never built because no pipeline references them); the profiles
   pipeline is kept with the `debug` exporter.
4. **otel-lgtm enables anonymous Grafana *Admin* by default.** Disabled here
   (`GF_AUTH_ANONYMOUS_ENABLED=false`) so the agent exercises real credentials.
5. **Demo `agent`/`chatbot`/`mcp` disabled**: the load generator's `ask_agent`
   task then fails, so `load-generator` client spans carry errors; demo rules
   exclude `service_name="load-generator"`.
6. **Span metrics need a few minutes after a (re)start** before rates are
   meaningful (counters start at 0).
7. **`kubectl create --raw` POSTs are rejected by Alertmanager (415)**;
   `fire-test-alert.sh` POSTs from inside the Alertmanager pod with busybox `wget`.
8. **WSL `~/.docker/config.json` uses `credsStore: wincred.exe`**, which fails in
   a login shell; the scripts that pull public images use a temporary empty
   `DOCKER_CONFIG`.
9. Thanos sidecar runs **without object storage** (StoreAPI over the live TSDB
   only). Prometheus still uses min = max block duration 2h so an objstore +
   store gateway can be added later.

## Files

```
test-infra/lgtm/
  k8s/observability/           kustomization (namespace observability)
    lgtm.yaml                  otel-lgtm + thanos sidecar, PVC, RBAC for cAdvisor scrape
    thanos-query.yaml, alertmanager.yaml, webhook-sinks.yaml,
    synthetic-exporter.yaml, expose.yaml (LoadBalancers), agent-rbac.yaml
    config/prometheus.yaml     upstream otel-lgtm 0.40.0 config + forseti additions
    config/alertmanager.yml, config/thanos-endpoints.yaml
    rules/{demo,kubernetes,synthetic}.yaml
    files/{webhook_sink,synthetic_exporter}.py
  helm/kube-state-metrics-values.yaml, helm/otel-demo-values.yaml
  scripts/                     lib.sh + the scripts above
```
