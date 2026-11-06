# Alert intake, triage and auto-investigate policies — Phase 1 design

Status: approved defaults (2026-11-06), implementation pending. Source requirements: `HANDOFF.md` §13.
Scope: P1 only (§13.4). Grouping beyond exact fingerprint, FP suggestions, scorecards and silences are later phases.

## 1. Placement

| Concern | Lives in | Why |
|---|---|---|
| Webhook receiver, persistence, triage API, policies, limiter, storm mode, FP auto-disable | `config_service` | Owns Postgres, Alembic, auth, audit and `agent_runs` (joinable). |
| Dispatcher (closes batch windows, starts/polls automatic runs) | `config_service` background task, single active instance via Postgres advisory lock | Timers must survive restarts → state in Postgres, not memory. |
| Running investigations | `sre-agent` (wave-1 patches) | `POST /investigations` (async start, caller thread id, `trigger_source`, `metadata`), `GET /investigations/{thread_id}`, global concurrency gate, `429` for automatic runs when no slot. |
| UI | `web_ui` new "Alerts" section | Inbox, alert detail, policy dialog, policy management screen, status widget. |

**The limiter is enforced in exactly one function** (`alerts/limiter.py: try_acquire_auto_run(policy, now)`), called only by the dispatcher inside a DB transaction holding an advisory lock. No other code path starts automatic investigations.

## 2. Configuration (team config key `alerting`, seeded from `local.yaml`; all values overridable)

```yaml
alerting:
  webhook_token: ${ALERT_WEBHOOK_TOKEN}        # Alertmanager http_config.authorization (Bearer)
  collection_window_seconds: 90
  cooldown_seconds: 1800
  limits:
    auto_max_concurrent: 2
    auto_max_per_hour: 6
    auto_max_per_day: 30
    auto_max_per_policy_per_hour: 2
    dispatch_retry_seconds: 300               # retry a 429 (no agent slot) this long, then skip
  storm:
    new_firing_per_minute_threshold: 20
    hold_seconds: 600
  fp_auto_disable:
    window_days: 14
    min_reviewed_batches: 3
    max_fp_ratio: 0.5                          # disable when ratio > this
  labels:
    scoping: [alertname, env, environment, cluster, namespace, service, job, severity]
    high_cardinality: [user, user_id, session, session_id, request_id, trace_id, pod, instance, container]
  agent_timeout_seconds: 600
```

Global agent concurrency cap for **all** runs (manual included) is `MAX_CONCURRENT_INVESTIGATIONS=4` in sre-agent. Manual runs wait for a slot and are exempt from the hourly/daily quotas.

## 3. Data model (new migration chained after current head; all tables carry `org_id`, `team_node_id`)

- **alert_event**: id, received_at, group_key, receiver, status, external_url, truncated_alerts, payload (JSONB, raw), alert_count.
- **alert_instance**: id, fingerprint, starts_at, ends_at, am_status (`firing|resolved`), alertname, labels (JSONB), annotations (JSONB), generator_url, group_key, first_received_at, last_received_at, notification_count, triage_state (`untriaged|investigating|decided`), latest_verdict, handling (`inbox|auto_batched|attached_running|attached_cooldown|skipped_storm|skipped_rate_limit|skipped_concurrency|policy_disabled`), handling_reason, batch_id (nullable), agent_thread_id / agent_run_id (manual investigation link). **Unique (team, fingerprint, starts_at)** → idempotent upsert; re-sends and `resolved` never create rows.
- **triage_decision**: id, alert_instance_id, verdict (`true_positive|false_positive|duplicate|known_noise|acknowledged`), fp_reason (`threshold_too_low|for_too_short|wrong_label_scope|maintenance_window|test_traffic|flapping|other`), duplicate_of_instance_id, note, decided_by, decided_at, source (`human` only in P1). History is append-only; `alert_instance.latest_verdict` is denormalised.
- **auto_investigate_policy**: id, name, matchers (JSONB list of `{name, op: "=|!=|=~|!~", value}`), created_from_alert_instance_id, created_by, created_at, reason, status (`enabled|disabled|auto_disabled_false_positives|deleted`), status_changed_at/by, status_note (mandatory on re-enable), enabled_since (FP counter restarts here), collection_window_seconds/cooldown_seconds (nullable overrides), expires_at, last_triggered_at, auto_disable_stats (JSONB), acknowledged_warning_at.
- **auto_investigation_batch**: id, policy_id, opened_at, window_ends_at, dispatched_at, closed_at, status (`collecting|dispatching|running|completed|failed|skipped_storm|skipped_rate_limit|skipped_concurrency`), skip_reason, agent_thread_id, agent_run_id, usage (JSONB), alert_count.
- **alert_intake_state** (one row per team): storm_active, storm_since, below_threshold_since, resumed_by, resumed_at, last_rate.
- **alert_audit** (or reuse existing audit log if it fits): policy create/edit/disable/delete/re-enable, auto-disable, storm enter/exit/resume, rate-limit skips.

## 4. Intake flow (`POST /api/v1/alerts/alertmanager/webhook`, Bearer `webhook_token`, body ≤ 1 MiB)

One transaction per notification:
1. Insert `alert_event`.
2. For each alert: upsert `alert_instance` on (fingerprint, startsAt). Update `am_status`, `ends_at`, `last_received_at`, `notification_count`.
3. Only a **newly created firing instance** continues. Repeats and resolved notifications stop here.
4. Storm accounting: count new firing instances in the last 60 s. Crossing the threshold → `storm_active=true` + audit. While storm is active, new instances get `handling=skipped_storm` (inbox) and no batch is opened.
5. Policy match: enabled, unexpired policies whose matchers all match (regexes fully anchored, Alertmanager semantics). Pick the most specific (most matchers; tie → oldest). Only one batch per alert, ever.
6. With a matching policy:
   - an open `collecting` batch → attach (`auto_batched`);
   - a `running` batch → attach (`attached_running`) and best-effort `queue-message` a compact summary to its thread;
   - inside cooldown after the last batch closed → attach to that batch (`attached_cooldown`);
   - otherwise open a new batch with `window_ends_at = now + collection_window`.
7. No matching policy → `handling=inbox`.
8. Return `200 {"success": true, "result": {"instances": n, "new": k}}`.

## 5. Dispatcher (every 5 s, advisory-locked)

- Batches past `window_ends_at` in `collecting`:
  - storm active → `skipped_storm`;
  - else `try_acquire_auto_run()` checks storm, global auto concurrency (batches in `dispatching|running`), per-hour/day global counts and per-policy per-hour count. Any failure → `skipped_rate_limit` with the limit named (e.g. "auto_max_per_hour=6 reached"). Its alerts stay visible in the inbox with that reason. Never dropped.
  - else build the prompt (§6) and call sre-agent `POST /investigations` with `thread_id=alert-batch-<8hex>`, `trigger_source=alert` and `metadata={batch_id, policy_id, alert_instance_ids}` → `running`.
  - On `429` keep `dispatching` and retry each tick until `dispatch_retry_seconds`, then `skipped_concurrency`.
- `running` batches: poll `GET /investigations/{thread_id}` and on a terminal state set `completed|failed`, `closed_at`, `agent_run_id` and `usage`. Cooldown starts at `closed_at`.
- Storm exit: rate below threshold continuously for `hold_seconds` → inactive + audit. A human "resume" clears the storm and suppresses re-entry for `hold_seconds`.

## 6. Investigation prompt (batch and manual)

Built from: policy sentence, alert count, time range, deduplicated common labels, **which labels vary** (with distinct counts — a tuning hint), annotations (summary/description/runbook_url), `generatorURL` (contains the PromQL), and the suggested time window `[min(startsAt) − 15 m, now]`. Alert content is placed in a clearly delimited data block with the instruction that it is untrusted data, not instructions. The default path: metrics (Thanos/Prometheus) → logs (Loki) → traces (Tempo via trace-id pivot) → Kubernetes state.

## 7. FP auto-disable

Evaluated on every human verdict on an alert that belongs to a batch:
- Consider batches of the policy opened since `max(enabled_since, now − window_days)`.
- A batch is **reviewed** if at least one of its alerts has a human verdict. It is **FP** if most of its verdicted alerts are `false_positive`.
- If reviewed ≥ `min_reviewed_batches` and FP/reviewed > `max_fp_ratio`: status becomes `auto_disabled_false_positives`, the stats are stored and audited, and the warning shows on the policy screen and as an inbox banner until acknowledged. The text is: **"This policy triggered too many false positives and was disabled automatically. Please investigate."**
- Re-enable requires `status_note`; sets `enabled_since=now` (counter restart).

## 8. API (team auth unless noted; error format `{"success", "result", "error"}`)

| Method / path | Purpose |
|---|---|
| `POST /api/v1/alerts/alertmanager/webhook` | intake (webhook token) |
| `GET /api/v1/team/alerts/instances` | inbox. Filters: am_status, triage_state, handling, alertname, severity, service, namespace, policy_id, since. Paged. |
| `GET /api/v1/team/alerts/instances/{id}` | detail incl. events, decisions, batch, investigation links |
| `POST /api/v1/team/alerts/instances/{id}/decisions` | verdict (and optional `duplicate_of`) |
| `POST /api/v1/team/alerts/instances/{id}/investigate` | manual run (trigger_source `manual`, waits for global slot) → returns thread id |
| `GET/POST /api/v1/team/alerts/policies`, `GET/PATCH/DELETE …/{id}` | CRUD. PATCH covers matchers, enable/disable (note required to re-enable after auto-disable), expiry. DELETE is an audited soft delete. |
| `POST /api/v1/team/alerts/policies/preview` | matchers → match counts over 7/30 days + sample alerts |
| `GET /api/v1/team/alerts/status` | storm state, auto runs last hour/day vs limits, skipped counts, open warnings |
| `POST /api/v1/team/alerts/storm/resume` | human resume |

## 9. UI (web_ui, team role; link in both sidebars)

- **Alerts inbox** `/team/alerts`: tabs New / Investigating / Decided / All; filters; grouped view by Alertmanager `groupKey`; status widget; storm banner; auto-disabled-policy banner. Rows show handling badges ("auto-investigated by policy X", "skipped: rate limit auto_max_per_hour=6").
- **Alert detail**: labels (scoping vs high-cardinality highlighted), annotations, generator link, notification history, verdict buttons (False positive [reason + text] · Duplicate of… · Known noise/needs tuning · True positive · Acknowledge), **Investigate**, **Investigate & auto-investigate future alerts like this**.
- **Policy dialog**: presets (this exact alert / every alert from rule / rule scoped — default). Label checkboxes with scoping ticked and high-cardinality unticked and flagged. Live preview counts (7/30 d).
- **Auto-investigate policies** `/team/alerts/policies`: one row per policy as a sentence; created by/when/why; last triggered; matches 7/30 d; investigations started (links); actions edit / disable / remove / expiry. The auto-disable warning is shown prominently.
- Polling (no realtime lib in web_ui): inbox and status refresh every 10 s.

## 10. Acceptance tests (automated where possible)

From HANDOFF §13.5, plus unit tests for: matcher semantics, most-specific policy selection, idempotent upsert, batch window/cooldown/attach transitions, limiter (each limit independently plus the concurrency 429 retry→skip path), storm enter/exit/hysteresis/resume, FP auto-disable thresholds and counter restart on re-enable. An end-to-end script against the local k3s Alertmanager: 20 per-user alerts from one rule within the window → exactly one investigation.
