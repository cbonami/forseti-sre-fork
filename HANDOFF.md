# HANDOFF — OpenSRE fork for the Grafana LGTM stack

Owner: Christian Bonami
Upstream: https://github.com/swapnildahiphale/OpenSRE (Apache 2.0)
Docs: https://opensre.in/docs
Status: research done in chat, no code written yet. This file is the brief for the Claude Code session.

---

## 1. Goal

Fork OpenSRE and turn it into a lean, LGTM-only AI SRE that runs entirely on a dev laptop with private keys:

1. **LGTM compatibility** — Loki (logs), Grafana (dashboards/alerts/datasources), Tempo (traces), Prometheus/Mimir (metrics). Tempo support must be *added*; it does not exist upstream.
2. **Prune** skills for tools not used (Datadog, Splunk, VictoriaLogs, Elasticsearch, etc.).
3. **Two local deployment paths**:
   - Docker Compose (fast inner loop).
   - Okteto on a local Kubernetes cluster (closer to the eventual AKS-style target).
4. Secrets come from private, uncommitted config only (Anthropic key, Grafana token, etc.).
5. **Alertmanager intake with human triage** (no automatic investigations unless a human creates a matcher-based auto-investigate policy, e.g. "all alerts of rule R in prd", manageable on a dedicated screen), duplicate/grouping help and false-positive learning to support alert tuning — see section 13 (phase 1 in scope, later phases design-open).

Non-goals for this session: production hardening, multi-tenant setup, SSO, getting the Teams bot running (keep its code; wiring it up is a later step).

Communication channel: **Microsoft Teams only. Slack is not used and is pruned** (see section 4).

---

## 2. What we know about upstream (verified from README + docs/ARCHITECTURE.md, Nov 2026)

Treat everything here as "verified from docs"; anything about code internals not listed here is **unverified — read the source first**.

**Runtime**
- Agent runtime is the **Claude Agent SDK** (`InteractiveAgentSession` in `sre-agent/agent.py`). No LangGraph.
- Root agent (`investigator`, fallback `planner`) dispatches specialist subagents via the SDK `Task` tool. Prompts and agent topology live in **config-service** (`agents.{id}.prompt.system`, `sub_agents`).
- Default server: `sre-agent/server_simple.py` (simple mode: cleartext HTTP, no rate limit, no sandbox — localhost only). Sandbox mode: `sre-agent/server.py` with per-thread K8s pods.
- Wall-clock cap `AGENT_TIMEOUT_SECONDS` (default 600s).

**Services and ports (`make dev`)**

| Service | Port | Role |
|---|---|---|
| Web UI (Next.js) | 3002 | Console, investigations, memory hub, config editor |
| sre-agent (FastAPI) | 8001 | `POST /investigate`, thread endpoints, SSE |
| config-service | 8081 | Hierarchical org→team config, tokens, agent-run storage |
| PostgreSQL | 5433 | Config + agent-run traces |
| Neo4j Browser / Bolt | 7475 / 7688 | Episodic memory + service topology |
| LiteLLM (optional) | 4001 | `--profile litellm` |

**Entry points**: web UI, Slack bot, Teams bot, REST (`POST /investigate`, `POST /threads/{thread_id}/queue-message`). There is **no** native Alertmanager/webhook receiver. Incidents are free-text prompts.

**Skills**
- Location: `sre-agent/.claude/skills/<id>/SKILL.md` (+ optional Python scripts run via Bash).
- After adding/removing a skill, regenerate the catalog: `config_service/scripts/gen_skills_catalog.py`.
- Skills can also be disabled per team in config; `ENABLED_SKILLS` env is empty by default so team-level toggles apply (from v1.1.0 release notes).
- Integration config pattern: `integrations.<name>` in config-service. Documented examples:
  - `integrations.prometheus.url` (+ optional `username`/`password`)
  - `integrations.grafana.url`, `integrations.grafana.api_key`
  - Loki: config fields **not documented** — read its `SKILL.md`.
- Known skill IDs from docs: `memory-search`, `infrastructure-neo4j`, `observability-splunk`, `alerting-opsgenie`. All other directory names are **unknown — list them, don't guess**.

**Other repo assets**: `docker-compose.yml`, `charts/opensre` (Helm chart), `Makefile` (`dev`, `stop`, `status`, `logs`, `clean`, `e2e-setup` with Kind + OTel demo, `e2e-test-*` fault injection), `.env.example`, `litellm_config.example.yaml`, `CLAUDE.md`, `SECURITY.md` (simple-mode posture + hardening checklist).

---

## 3. First actions in the Claude Code session

```bash
gh repo fork swapnildahiphale/OpenSRE --clone
cd OpenSRE
git remote -v
git checkout -b lgtm
```

Then, before changing anything:

1. Read `CLAUDE.md`, `SECURITY.md`, `docker-compose.yml`, `Makefile`, `.env.example`, `charts/opensre/values.yaml`.
2. Inventory skills and produce a keep/prune table:
   ```bash
   ls -1 sre-agent/.claude/skills
   ```
3. Read the Loki, Grafana, Prometheus/metrics, Jaeger and Kubernetes skills in full. The Jaeger skill is the template for the new Tempo skill.
4. Read `config_service/scripts/gen_skills_catalog.py` to understand how the catalog is built and whether pruned skills leave references elsewhere (seed config, default team config, web UI skill tree, tests).
5. Grep for hard references to pruned tools before deleting:
   ```bash
   grep -rniE "datadog|splunk|victoria|elasticsearch|coralogix|honeycomb|newrelic|new_relic|slack" --include=*.py --include=*.ts --include=*.tsx --include=*.yaml --include=*.yml --include=*.json --include=*.md .
   ```

---

## 4. Skill pruning

**Prune (confirm exact directory names from the inventory):**
- Observability: Datadog (incl. APM), Splunk, VictoriaLogs, VictoriaMetrics, Elasticsearch, Coralogix, New Relic, Honeycomb, Sentry (unless wanted), Jaeger (after Tempo skill exists — or keep as reference until then)
- Cloud: AWS, GCP (keep Azure + Kubernetes; Docker optional)
- Alerting/incident: Opsgenie, Incident.io, Blameless, FireHydrant, PagerDuty (unless wanted)
- Data: Snowflake, BigQuery, MySQL
- Other: Vercel, Amplitude, ClickUp, Linear, Notion, Google Docs, Sourcegraph, Bitbucket

**Prune Slack completely** (not used; Teams only):
- the Slack bot (upstream README/Makefile refer to `slack-bot/`, but the repo tree shows `teams-bot/` and no `slack-bot/` at top level — locate the actual Slack bot code first),
- the Slack compose profile (`--profile slack`) and `make dev-slack` target,
- `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` from `.env.example` and docs,
- the Slack skill (agent reading incident channels), if present,
- Slack references in prompts, UI and docs.
Keep `teams-bot/`, `make dev-teams` and the Teams profile.

**Keep:**
- Core methodology skills (investigation framework, observability methodology, infra debugging, remediation)
- `memory-search`, `infrastructure-neo4j`
- Grafana, Prometheus/metrics analysis, Loki
- Kubernetes, Azure
- GitHub / GitLab (pick what's actually used), Jira + Confluence (decide), PostgreSQL, Argo CD, Kafka (optional), flagd/OpenFeature (needed if we keep the OTel demo fault-injection tests)

**Approach decision (make it explicitly in the session):**
- Option A — delete skill directories in the fork. Clean, but increases merge conflicts with upstream.
- Option B — keep directories, disable via team config / `ENABLED_SKILLS`. Upstream-friendly, but the agent's skill catalog and UI still show them.
- Recommendation: **A**, done as one isolated commit ("prune non-LGTM skills") so it can be re-applied after upstream merges. Regenerate the catalog in the same commit.

---

## 5. LGTM work items

### 5.1 Tempo skill (new) — main piece of work
Create `sre-agent/.claude/skills/observability-tempo/SKILL.md` (name to match the existing naming convention once seen) plus a script, modelled on the Jaeger skill.

Tempo HTTP API endpoints to use (confirm against the Tempo version we run):
- `GET /api/traces/{traceID}` — fetch one trace
- `GET /api/search?q={TraceQL}&start=&end=&limit=` — TraceQL search
- `GET /api/search/tags`, `GET /api/v2/search/tag/{tag}/values` — discovery
- `GET /api/echo` / `/ready` — health

Skill must support:
- Fetch a trace by ID and summarise span tree, slowest spans, error spans, service hops.
- TraceQL search by service, status, duration (e.g. errors in a service in the incident window).
- **Loki → Tempo pivot**: extract `trace_id`/`traceID` from Loki log lines and fetch the trace. This is the key correlation upstream lacks.
- Config: `integrations.tempo.url` and optional auth (bearer / basic / Grafana Cloud tenant header `X-Scope-OrgID` for multi-tenant setups).
- Option to query Tempo **via Grafana's datasource proxy** instead of direct, so a single Grafana token suffices. Verify the Grafana API path before implementing; do not guess.

Reference material: Grafana Labs publishes official agent skills for the LGTM stack (incl. Tempo). Worth reading for TraceQL guidance, but adapt to OpenSRE's config/credential conventions rather than dropping it in.

### 5.2 Mimir compatibility
Mimir exposes a Prometheus-compatible query API under a prefix (default `/prometheus`). Check whether the Prometheus skill accepts a base URL with a path prefix and an `X-Scope-OrgID` header. Add both if missing.

### 5.3 Loki skill
- Document its config fields (missing from upstream docs).
- Add multi-tenant header support if missing.
- Ensure it surfaces trace IDs so 5.1's pivot works.

### 5.4 Grafana skill
- Confirm it uses a **service account token** (Grafana API keys are deprecated); keep the config key `api_key` for compatibility but document it as a service account token.
- Check whether it can list datasources and resolve Loki/Tempo/Prometheus datasource UIDs — useful for the proxy option.

### 5.5 Prompts / methodology
Update the investigator prompt and observability methodology skill so the default path is: Prometheus/Mimir metrics → Loki logs → Tempo traces (via trace-ID pivot) → K8s state. Remove references to pruned tools from prompts (they live in config-service seed data — find where).

### 5.6 Alertmanager integration
Promoted from "optional bridge" to a feature in its own right: alert intake with **human triage** before investigation. See section 13.

---

## 6. Local LGTM backend

Two options; pick one in the session:

- **A. `grafana/otel-lgtm`** single container (Grafana + Prometheus + Loki + Tempo + Pyroscope + OTel Collector). Grafana on 3000 — no clash with OpenSRE's 3002. OTLP on 4317/4318. Easiest for a self-contained laptop setup. Check its current image docs for port/datasource details.
- **B. Existing private Grafana stack** (homelab or Grafana Cloud free tier) reached via URL + token from `.env`.

For realistic incidents, we need a workload emitting telemetry:
- Reuse upstream `make e2e-setup` (Kind + OpenTelemetry Demo) but reconfigure the demo's collector to export traces to Tempo and logs to Loki instead of Jaeger/OpenSearch. Confirm what the upstream e2e scripts install before changing them.
- Keep the `e2e-test-*` fault-injection targets working against the LGTM backend — these are our acceptance tests.

---

## 7. Docker Compose deployment

Target: `make dev` equivalent with LGTM, no cloud dependencies besides the LLM API.

- Add `docker-compose.lgtm.yml` as an **overlay** on upstream `docker-compose.yml` (minimises upstream conflicts) that:
  - adds the `otel-lgtm` service (option A) on a shared network,
  - injects LGTM integration env/config into sre-agent / config-service,
  - optionally adds a lightweight telemetry generator if the OTel demo is too heavy for compose.
- Add Makefile targets, e.g. `dev-lgtm`, `stop-lgtm`, mirroring existing ones.
- Find out how upstream seeds integration config (env vars vs config-service API vs seed file) and make LGTM integrations configured automatically at startup — no manual UI clicking after `make dev-lgtm`.

---

## 8. Okteto deployment (local Kubernetes)

Target: same stack on a local cluster, deployed with Okteto, with hot-sync of `sre-agent` code.

- Cluster: Kind (upstream already uses it) or k3d. Decide in session.
- Deploy via upstream Helm chart `charts/opensre` with a `values-lgtm.yaml` overlay; LGTM backend via its own chart/manifest or the `otel-lgtm` image as a Deployment.
- `okteto.yml` manifest:
  - `deploy:` runs the Helm installs (OpenSRE chart + LGTM backend),
  - `dev:` entry for `sre-agent` (file sync of `sre-agent/`, port-forward 8001), optionally `web_ui` (3002).
- **Verify** which Okteto features work with the open-source CLI against a plain local cluster vs. requiring Okteto Platform. Check current Okteto docs before writing the manifest — do not assume manifest fields.
- Secrets as Kubernetes Secrets created from the local `.env`; never baked into values files.
- Stay in simple mode first; sandbox mode (`server.py`) is a later experiment.

---

## 9. Secrets & safety

- `.env` stays untracked (check `.gitignore` covers it plus any new overlay env files, e.g. `.env.lgtm`).
- Required: `ANTHROPIC_API_KEY`. Grafana: service account token with **Viewer** role (read-only). Loki/Tempo/Prometheus: read-only credentials where auth is used.
- Kubernetes skill on the local cluster: use a read-only ServiceAccount/kubeconfig for the agent, not the admin context.
- Never point this at Acerta environments from the laptop. Private keys and private/dev data only.
- Bind everything to localhost; read `SECURITY.md` simple-mode notes.

---

## 10. Acceptance criteria

1. `make dev-lgtm` brings up OpenSRE + LGTM; web UI at http://localhost:3002 works with the admin token.
2. Catalog lists only kept skills; no references to pruned tools in prompts or UI.
3. A free-text investigation ("checkout is returning errors") against the demo workload uses Prometheus → Loki → Tempo and reports a root cause with evidence including at least one fetched trace.
4. At least two upstream fault-injection scenarios pass against the LGTM backend.
5. `okteto deploy` (or the equivalent verified command) brings up the same stack on the local cluster; `okteto up` syncs `sre-agent` changes.
6. Fork changes are split into reviewable commits: prune / Tempo skill / Loki+Mimir tweaks / compose overlay / Okteto / docs.
7. `docs/LGTM.md` in the fork describes setup, config keys and known gaps.

---

## 11. Open questions to settle early

- Keep Jira/Confluence/GitHub/GitLab skills, or strictly LGTM + K8s?
- LGTM backend: `otel-lgtm` container vs existing private Grafana stack?
- Kind vs k3d for the Okteto path?
- Track upstream (rebase regularly) or treat the fork as diverged?
- LLM provider: direct Anthropic only, or LiteLLM profile to also test a local model?

## 12. Working rules for the session

- Verify before asserting: read source/docs for APIs, config keys and CLI flags; don't invent them.
- No placeholder syntax in commands handed to the user — use real values or named env vars.
- Small commits, each with a one-line rationale.

---

## 13. Alert intake, human triage and alert tuning (new feature)

**Status: requirements clear, design open.** Christian will iterate on the design with Claude in chat; the ideas below are a starting point, not decisions. Build phase 1 only unless told otherwise.

### 13.1 Intent
Help SREs **tune alerts and eliminate false positives**, not just investigate them. By default alerts never trigger an investigation automatically: a human decides. A human can create **auto-investigate policies** (label matchers, typically "every alert of rule R", optionally narrowed) so matching alerts are investigated without triage.

Requirements:
1. Prometheus **Alertmanager** sends alerts to OpenSRE via a webhook receiver.
2. Alerts land in a **triage inbox** in the web UI. A human decides per alert (or per group): investigate, false positive, duplicate, ignore/known noise.
3. "Investigate" starts a normal OpenSRE investigation with the alert as context.
3a. From triage of alert A (rule R), a human can decide that **future alerts from the same rule R are investigated automatically**, optionally narrowed to labels of A (env, service, namespace…). A dedicated screen lists all these auto-investigate policies and lets a user disable or remove them.
4. The app helps find **doubles**: the same underlying problem firing repeatedly (e.g. one alert per end user hitting the same broken transaction) and **groups** related alerts.
5. **False-positive verdicts are recorded** and used by the AI to recognise likely false positives in new alerts (as a suggestion, human confirms).
6. Longer term: per-rule insight to tune thresholds, `for` durations, label cardinality and routing.

### 13.2 Facts to build on (verify against the Alertmanager version in use)
- Alertmanager webhook payload (`version: "4"`) carries `groupKey`, `status`, `receiver`, `groupLabels`, `commonLabels`, `commonAnnotations`, `externalURL`, and `alerts[]` with `status`, `labels`, `annotations`, `startsAt`, `endsAt`, `generatorURL`, `fingerprint`.
- `fingerprint` is a stable hash of the alert's label set → exact-duplicate detection comes for free.
- Alertmanager already groups via `group_by` in its routing tree; our grouping should complement, not fight, that config.
- Alertmanager re-sends notifications per `repeat_interval` and sends `resolved` when `send_resolved: true` → the receiver must be idempotent (upsert on fingerprint + `startsAt`).
- Silences can be created via Alertmanager API v2 (`/api/v2/silences`) → possible "silence from triage" action, **human-approved only**.
- Grafana-managed alerting can also emit webhooks, but its payload differs in details → Prometheus Alertmanager first, Grafana alerting later.

### 13.3 Initial design ideas (to discuss, not to implement blindly)

**Intake**
- New endpoint (in sre-agent or a small separate `alert-intake` service — decide after reading the code) with a shared-secret or bearer auth; Alertmanager `webhook_configs` points at it.
- Persist in PostgreSQL: `alert_event` (each notification), `alert_instance` (unique by fingerprint + startsAt), `alert_group`, `triage_decision` (who, when, verdict, reason, free text).

**Triage UI** (Next.js web_ui, new "Alerts" section)
- Inbox: new / in triage / investigating / closed, filter by rule, severity, service, namespace.
- Actions: Investigate · Investigate & auto-investigate future alerts like this (opens the policy dialog) · False positive (with reason category + text) · Duplicate of… · Known noise / needs tuning · Acknowledge.
- Alerts that were auto-investigated still appear in the inbox, marked as such and linked to their investigation, so humans can still record a verdict.
- Investigate pre-fills a prompt from alert labels, annotations, `generatorURL` and time window, then calls the existing investigation flow; link investigation ↔ alert.

**Auto-investigate policies** (new screen: "Auto-investigate policies")

*Model.* A policy is a set of **label matchers** in Alertmanager matcher syntax (`=`, `!=`, `=~`, `!~`), the same model SREs know from silences. "Same alert" and "same rule" are both special cases:

| Preset in the dialog | Matchers (built from alert A's labels) |
|---|---|
| This exact alert | all of A's labels (≡ A's fingerprint) |
| Every alert from rule R | `alertname="R"` |
| Rule R, scoped (default) | `alertname="R"` + scoping labels of A the user ticks, e.g. `env="prd"`, `service="payment"` |

- Dialog starts from alert A and shows its labels as checkboxes. Default ticks: `alertname` plus known scoping labels (env/environment, cluster, namespace, service/job — make the list configurable). High-cardinality labels (user, session, request id, pod, instance) are unticked by default and flagged as such.
- Live preview: "this policy would have matched N alerts in the last 7/30 days" (from `alert_instance` history), so the user sees the blast radius before saving.
- Why matchers rather than a rule ID: the Alertmanager webhook payload carries no rule identifier. `alertname` is the de facto rule name, but the same `alertname` can be defined in several rule groups or come from several Prometheus instances. Matchers make the intended scope explicit. `generatorURL` (which encodes the rule's PromQL) is stored for display/debugging but not used for matching.

*Batching — one investigation per policy per window, not per alert.* A rule-scoped policy can match dozens of alerts at once (generic rules across services, per-user alerts). Behaviour:
- When an alert matches an enabled policy and no batch is open for that policy, open a batch with a short **collection window** (configurable, e.g. 60–120 s), then start **one** investigation whose prompt includes all alerts collected in the window (deduplicated labels, which labels vary, count, time range, `generatorURL`).
- Alerts matching while that investigation is running are attached to it (via the mid-run queue `POST /threads/{thread_id}/queue-message`, or recorded and shown — decide after reading the code) instead of starting a new run.
- After the run ends, a **cooldown** per policy applies; matches during cooldown are attached to the last investigation and shown in the inbox, not re-investigated.
- Repeats (`repeat_interval`) and `resolved` notifications never open a batch.
- Batching is the first defence; the hard rate limits below are the last one.
- If an alert matches several policies, it joins the batch of the most specific one (most matchers); never start two investigations for the same alert.

*Data model.*
- `auto_investigate_policy`: id, name, matchers (JSON), created_from_alert_instance, created_by, created_at, reason, enabled, collection_window, cooldown, optional expiry, last_triggered_at.
- `auto_investigation_batch`: id, policy_id, opened_at, closed_at, investigation/thread id, status.
- `alert_instance.batch_id` links each auto-handled alert to its batch.

*Management screen.* One row per policy, readable as a sentence ("auto-investigate `HighLatency` where `env=prd`, `service=payment`"), with: created by/when/why, last triggered, alerts matched (7/30 d), investigations started, links to them. Actions: edit matchers (with the same preview), disable, remove (audited soft delete so history stays), set/clear expiry.

*Inbox.* Auto-handled alerts still appear, marked "auto-investigated by policy X" and linked to the batch's investigation, so humans can still record verdicts.

**Investigation storm protection — hard requirement**

Principle: an alert storm must never become an investigation storm. Several independent layers; the rate limiter is the last line of defence and is never bypassed by any policy, batch or code path that starts investigations automatically.

1. *Batching per policy* (above).
2. *Storm detection on intake.* If the alert intake rate exceeds a threshold (e.g. N new firing instances per minute, configurable), switch to **storm mode**: all auto-investigation pauses, alerts go to the inbox, a prominent banner explains why. Leaving storm mode requires the rate to drop below the threshold for a hold period (hysteresis), or a human to resume.
3. *Hard rate limits on automatic investigations* (enforced in one place, at the point where an automatic run is started — not in the UI):
   - max concurrent automatic investigations (global),
   - max automatic investigations per hour and per day (global, token-bucket style),
   - max automatic investigations per policy per hour/day,
   - optional daily LLM token/cost budget if usage data is available from the SDK (verify).
   When a limit is hit, the alert is **not dropped**: it lands in the inbox as "auto-investigation skipped: rate limit reached (limit X)", and the event is logged/audited. Limits are config values with conservative defaults.
4. *Manual investigations* started by a human are outside the automatic limits but still subject to the global concurrency cap, so a human can always act during a storm. (Confirm in session.)
5. *Visibility.* A small status widget (inbox + policy screen): automatic runs in the last hour/day vs. limits, storm mode on/off, number of alerts skipped due to limits.

**Automatic disabling of policies with too many false positives**

- After each batch investigation, the human verdicts on its alerts feed the policy's FP statistics. Only **human** verdicts count; unreviewed alerts and AI-suggested verdicts do not.
- A policy is auto-disabled when, within a rolling window, it has at least a minimum number of reviewed batches (e.g. 3) **and** its FP ratio exceeds a threshold (e.g. 50 %). Thresholds are configurable; defaults conservative. Avoid disabling on a single verdict.
- On auto-disable:
  - status becomes `auto_disabled_false_positives` (distinct from a human `disabled`), with timestamp and the statistics that triggered it,
  - the policy row on the management screen shows a clear warning: **"This policy triggered too many false positives and was disabled automatically. Please investigate."** — with the FP count/ratio, links to the batches and verdicts, and a shortcut to the tuning view for rule R,
  - a banner/notification in the inbox shows the same message until acknowledged,
  - matching alerts go back to normal human triage in the inbox.
- Re-enabling requires a human action with a mandatory note (what was changed: rule threshold, matchers narrowed, …); audited. The FP counter restarts after re-enable.
- Alerts from a disabled policy are themselves useful tuning input: they point at rule R as a false-positive generator (see Tuning assistant).

**Doubles & grouping — layered, cheap first**
1. Exact: same `fingerprint`.
2. Structural: same `alertname` + service labels, ignoring **high-cardinality labels** (user id, session, request id, pod). Detecting which labels vary is itself a tuning finding ("this rule creates one alert per user — aggregate in the PromQL").
3. Temporal: alerts that co-fire within a window across rules (storm / cascade).
4. Semantic: embedding similarity on annotations/summary (Neo4j vector index already exists for episodes).
5. Topological: use the Neo4j service graph to group alerts on dependent services (blast radius).

**False-positive learning**
- Store verdicts as first-class data linked to the alert rule (and in Neo4j alongside episodes, e.g. `:AlertVerdict` → `:AlertRule`, so `memory-search` can use them).
- On new alerts, compute a *suggested* verdict with reasoning and similar past verdicts; show it in the inbox. Never auto-close in early phases; measure suggestion accuracy against human decisions first.

**Tuning assistant**
- Per-rule scorecard: fire count, FP rate (from verdicts), flapping (fire/resolve cycles), median duration, % resolved without human action, label cardinality.
- "What-if" backtest: replay a rule's PromQL via Prometheus `query_range` with a proposed threshold / `for` and show how many past firings (and how many confirmed FPs) it would have avoided.
- Output as a proposed change to the rules file / PrometheusRule CR, ideally a PR in the GitOps repo (ArgoCD-managed), reviewed by a human.

### 13.4 Phasing
1. **P1** — webhook receiver, persistence, inbox UI, manual triage verdicts, "Investigate" button. Exact-dup via fingerprint. Matcher-based auto-investigate policies (presets: exact alert / rule / rule scoped), batching per policy with collection window + cooldown, the "Auto-investigate policies" management screen, **storm protection (storm mode + hard rate limits)** and **automatic disabling of policies with too many false positives**, with the user-facing warning.
2. **P2** — structural + temporal grouping, high-cardinality label detection.
3. **P3** — FP verdict memory and AI-suggested verdicts (with measured accuracy).
4. **P4** — rule scorecards, backtesting, tuning proposals as PRs; human-approved silences.

### 13.5 Dev/test setup
- Alertmanager: check whether the chosen LGTM backend includes one; otherwise add `prom/alertmanager` to the compose overlay and the Okteto deploy.
- Use the upstream fault-injection targets plus a few deliberately noisy rules (low threshold, per-user labels, flapping) to generate both real incidents and false positives.
- Acceptance for P1: a firing alert appears in the inbox within one Alertmanager notification cycle; a human can mark it FP or start an investigation; resolved notifications update status; re-sends don't create duplicates. After creating a policy `alertname="R"` from alert A: a burst of alerts from rule R with different high-cardinality labels (e.g. 20 per-user alerts within the collection window) starts **exactly one** investigation containing all of them; re-sends start none; alerts during the run/cooldown are attached, not re-investigated; alerts from other rules still go to the inbox; the policy appears on the management screen with correct match/investigation counts; after disabling it, the next alert from R goes back to the inbox.
- Storm acceptance: firing many alerts across several policies never exceeds the configured concurrency / per-hour / per-day limits; skipped alerts appear in the inbox with the rate-limit reason; exceeding the intake threshold activates storm mode with a visible banner and pauses all automatic runs. Write automated tests for the limiter and storm-mode transitions, not just manual checks.
- FP auto-disable acceptance: marking the alerts of enough batches of a policy as false positive (above the configured ratio and minimum) sets the policy to `auto_disabled_false_positives`, shows the warning text on the management screen and in the inbox, sends subsequent matching alerts to triage, and re-enabling requires a note.

### 13.6 Open questions for the design discussion
- Triage unit: individual alert, Alertmanager group, or our own group?
- Auto-investigate policies: default collection window and cooldown? Which labels count as "scoping" vs "high-cardinality" by default (configurable list vs. learned from data)? Who may create/remove policies (any user vs. role)? Defaults for storm threshold, rate limits and FP auto-disable (window, minimum reviewed batches, ratio)? Do manual investigations share the concurrency cap with automatic ones? Should storm mode and auto-disabled policies also be posted to a Microsoft Teams channel (via the existing Teams bot or an incoming webhook/Workflows), and from which phase?
- Does a verdict apply to one instance, to the fingerprint, or to the rule pattern?
- Taxonomy of FP reasons (threshold too low, `for` too short, wrong label scope, maintenance window, test traffic, …)?
- Where does intake live: inside sre-agent or separate service?
- Should OpenSRE own silences, or only propose them?
- Upstream contribution: is this generic enough to offer back to OpenSRE?
