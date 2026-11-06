# OpenSRE - AI SRE Agent

AI-powered SRE agent for automated incident investigation using the Claude Agent SDK.

## Quick Start

```bash
cd sre-agent

# Setup
uv venv && source .venv/bin/activate
uv pip install -e .

# Configure
cp env.example .env
# Set ANTHROPIC_API_KEY in .env — no LiteLLM or OpenRouter required

# Run server (simple mode — Claude Agent SDK engine)
python server_simple.py
```

### Docker Compose (POC bring-up)

```bash
# From repo root
docker compose -f docker-compose.yml -f docker-compose.override.yml up sre-agent
```

## Agent Configuration

The agent supports rich configuration via config_service for team-specific behavior:

### Agent Config Fields

Each agent in your team config supports:

- **`enabled`** (bool): Whether this agent is active
- **`description`** (str): Routing hint the SDK uses to select this sub-agent
- **`prompt.system`** (str): Agent's system prompt defining its role and behavior
- **`tools.enabled`** (list): Allowed tools (`["*"]` for all)
- **`tools.disabled`** (list): Tools to exclude from enabled set
- **`model.name`** (str): Model to use for this agent, or `"inherit"` for the root model
- **`max_turns`** (int): Maximum conversation turns (prevents infinite loops)
- **`sub_agents`** (object): Allowed child agents `{agent_name: bool}` for routing enforcement
- **`skills`** (object): Per-agent skill enable/disable overrides

Only `model.name` is expressible through the Claude Agent SDK — sampling
parameters (`temperature`, `top_p`) and response limits (`max_tokens`) have
no SDK equivalent and are not configurable.

### Example Configuration

```json
{
  "agents": {
    "planner": {
      "enabled": true,
      "model": { "name": "inherit" },
      "max_turns": 50,
      "prompt": {
        "system": "You are an SRE investigator specialized in incident analysis..."
      },
      "tools": {
        "enabled": ["*"],
        "disabled": ["Write", "Edit"]
      },
      "sub_agents": { "kubernetes": true, "metrics": true }
    },
    "kubernetes": {
      "enabled": true,
      "max_turns": 30,
      "description": "Use for pod crashes, deployments, resource issues",
      "prompt": {
        "system": "You are a Kubernetes specialist..."
      }
    },
    "log_analysis": {
      "enabled": true,
      "max_turns": 20,
      "description": "Use for analyzing application logs and error patterns",
      "prompt": {
        "system": "You are a log analysis specialist..."
      }
    }
  }
}
```

### Execution Limits

- **`max_turns`**: Prevents runaway sessions by capping tool-use turns (each thought+tool pair = 1 turn)
- When exceeded, investigation returns partial results with status="incomplete"
- Set per root agent in **Admin → Configuration → Agents** (config-service hierarchical merge; planner default is 50)
- **Not** an `.env` variable — `sre-agent` reads `max_turns` only from team config
- Wall-clock timeout: `AGENT_TIMEOUT_SECONDS` in `docker-compose.yml` (default 600). Applies to every run, including automatic ones.

### Runtime controls (simple mode)

| Env var | Default | Effect |
|---|---|---|
| `MAX_CONCURRENT_INVESTIGATIONS` | `4` | Global cap on investigations executing at once, across web UI, Teams, API and alerts. Manual runs wait in FIFO order (the SSE stream gets a `queued` event first). Automatic runs (`trigger_source: "alert"`) never wait: HTTP 429 with `result.reason = "concurrency_limit"`. |
| `SESSION_IDLE_TTL_SECONDS` | `1800` | Each thread keeps one SDK session (one Claude CLI subprocess) for follow-ups. A session with no activity for this long is closed; the next message on the thread starts a new session that resumes the SDK conversation. `0` disables the reaper. Automatic threads close their session as soon as their run ends. |
| `AGENT_INTERNAL_TOKEN` | unset | Shared secret for `POST /investigations` and `GET /investigations/{thread_id}` (`Authorization: Bearer <token>`). Required when set; unset means no auth (local dev only, logged as a warning at startup). |

Automatic runs (`trigger_source: "alert"`) are non-interactive: `AskUserQuestion` is removed from the agent's tools and denied if called, so an unattended run never blocks on a question.

`GET /health` includes `concurrency: {limit, active, waiting}`.

## API

### Streaming investigation (`POST /investigate`)

```bash
curl -X POST http://localhost:8001/investigate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What files are in this directory?"}' \
  --no-buffer
```

Returns an SSE stream. Each line is `data: {"type", "data", "thread_id", "timestamp"}`.
Optional body fields: `thread_id`, `images`, `file_attachments`,
`resume_session_id`, `trigger_source` (`web_ui`, `teams`, `slack`, `api`,
`scheduled`, `manual`, `alert`; unknown values are stored as `web_ui`),
`trigger_actor`, and `metadata` (JSON object, max 16 KiB, stored on the agent
run as `extra_metadata`).

Event types added by the runtime controls (clients that do not know them ignore them):

- `queued` — `{position, active, limit}`; emitted before `run_started` when all slots are busy.
- `result` — besides `text`, `success`, `subtype`, now carries `usage`
  (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`), `total_cost_usd` and `model_usage` (per model,
  SDK camelCase keys, includes subagents) when the SDK reported them. Values are
  for this turn only: the SDK reports running totals per CLI process, the agent
  subtracts the previous turn. Caveat (unverified at runtime): the bundled CLI
  can restore a saved session's cost totals on resume; if it does, the first
  turn after a resume over-reports by that amount.

`/investigate` returns a JSON error envelope (below) instead of a stream for:
429 `concurrency_limit` (alert runs only), 409 `thread_active` (alert run on a
thread with a live session, or any message to a thread whose run was started
via `POST /investigations` and is still active), 400 `invalid_request`
(bad `metadata`).

### Async start and status (`POST /investigations`, `GET /investigations/{thread_id}`)

For callers that should not hold an SSE connection (e.g. the Alertmanager
dispatcher). Responses use the project format
`{"success": bool, "result": ..., "error": "..."}`. Auth: `Authorization:
Bearer $AGENT_INTERNAL_TOKEN` when set. Tenancy: optional
`X-OpenSRE-Team-Token` header (else `OPENSRE_TENANT_ID` / `OPENSRE_TEAM_ID`).

```bash
curl -s -X POST http://localhost:8001/investigations \
  -H "Authorization: Bearer $AGENT_INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "Investigate alert KubePodCrashLooping in namespace payments ...",
        "thread_id": "alert-batch-6f1c2d",
        "trigger_source": "alert",
        "trigger_actor": "alert-policy:p-42",
        "metadata": {"alert_ids": ["a1", "a2"], "policy_id": "p-42", "batch_id": "6f1c2d"}
      }'
# 202 {"success": true, "error": null,
#      "result": {"thread_id": "alert-batch-6f1c2d", "run_id": "<hex>",
#                 "status": "running", "trigger_source": "alert",
#                 "concurrency": {"limit": 4, "active": 1, "waiting": 0}}}
```

- `thread_id` is required and caller-chosen: 1-200 chars of
  `[A-Za-z0-9._:-]`, starting with a letter or digit. It is the
  `correlation_id` of the agent run in config-service.
- `trigger_source` defaults to `api` (manual: waits for a slot, `status:
  "queued"`). `alert` is automatic: started immediately or rejected.
- `run_id` is the id the agent run is recorded under in config-service
  (`agent_runs.id`, best-effort: if config-service is down the run still
  executes and status shows `run_recorded: false`).

Errors (`result.reason`): 400 `invalid_request`, 401 `unauthorized`,
409 `thread_active` (thread already has a live session; send follow-ups with
`POST /threads/{thread_id}/queue-message` while it runs), 429
`concurrency_limit` (`result` also has `limit`, `active`, `waiting`), 503
`not_configured` (no `ANTHROPIC_API_KEY`).

```bash
curl -s http://localhost:8001/investigations/alert-batch-6f1c2d \
  -H "Authorization: Bearer $AGENT_INTERNAL_TOKEN"
# 200 {"success": true, "error": null, "result": {
#   "thread_id": "...", "run_id": "...", "run_recorded": true,
#   "status": "completed",            # queued | running | completed | failed | interrupted | timeout
#   "trigger_source": "alert", "trigger_actor": "alert-policy:p-42",
#   "metadata": {...},
#   "queued_at": "...", "started_at": "...", "completed_at": "...", "duration_seconds": 312.4,
#   "usage": {"total_cost_usd": 0.41, "input_tokens": 1520, "output_tokens": 3110,
#             "cache_read_input_tokens": 88000, "cache_creation_input_tokens": 9100,
#             "model_usage": {...}},
#   "error": null, "session_active": false}}
```

The status is in memory (latest run per thread) and is lost on restart; 404
`not_found` for unknown threads. The durable record is the config-service
agent run (`GET /api/v1/internal/agent-runs/{run_id}`), which now also stores
`cost_usd`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens` and `model_usage`.

## Architecture

### Local Development
```
Request → server_simple.py:8001 → agent.py (Claude Agent SDK) → Stream Results
                                        ↓
                               InteractiveAgentSession
                               (direct Anthropic API, ANTHROPIC_API_KEY)
```

The agent runs in-process via the Claude Agent SDK (`claude-agent-sdk`). LLM calls go directly to Anthropic — no LiteLLM proxy, no OpenRouter required.

## Integrations

Integrations are implemented via **skills with Python scripts**, not MCP tools. This keeps the agent's context clean and enables progressive disclosure of knowledge.

### Available Integrations

| Integration | What It Provides | Environment Variables |
|-------------|------------------|----------------------|
| **Kubernetes** | Pod inspection, logs, events, resource status | `KUBECONFIG` (auto-detected) |
| **Loki** | Logs (LogQL) | `LOKI_URL` |
| **Grafana / Prometheus** | Dashboards, PromQL metrics | `GRAFANA_URL`, `GRAFANA_API_KEY`, `PROMETHEUS_URL` |
| **Git** | Commit history, deployment correlation | Always available (uses local git) |

### How Integrations Work

Each integration is a skill containing:
- **SKILL.md** — methodology and reference documentation
- **scripts/** — Python scripts that call the actual APIs (Kubernetes, Loki, etc.)

When the agent needs to use an integration:
1. Reads the skill metadata (progressive disclosure)
2. Executes relevant Python scripts via Bash
3. Gets structured output without bloating context with tool descriptions

See `env.example` for all available integrations.

## Skills

**22 skills** organized by category provide on-demand methodology and best practices:

| Category | Skills |
|----------|--------|
| **Observability** | Loki, Grafana, Jaeger, metrics analysis (Prometheus) |
| **Alerts** | alerting-context |
| **Infrastructure** | Kubernetes, Docker, Azure, Neo4j |
| **Databases** | PostgreSQL |
| **Streaming** | Kafka |
| **Platform** | Argo CD, flagd (OpenFeature) |
| **Project & Docs** | GitHub, Jira, Confluence knowledge base |
| **Investigation** | Root cause analysis, observability methodology, metrics analysis, remediation, knowledge base (RAPTOR), memory search |

Skills are automatically invoked when relevant to the task. Located in `.claude/skills/` directory. Skills can be filtered per-agent via config-service (see `docs/SKILLS_FILTERING.md`).

## Episodic Memory

The agent has episodic memory that stores and retrieves past investigation episodes:

- **Pre-investigation**: Similar past episodes and strategies injected into agent context
- **Post-investigation**: LLM extracts structured episode metadata (summary, root cause, services, severity)
- **Strategy generation**: After 2+ similar episodes, LLM generates reusable investigation strategies

Memory is stored in PostgreSQL via config-service. See `docs/MEMORY_SYSTEM.md`.

Key files:
- `memory/integration.py` — memory enhancement and episode storage
- `memory/strategy_generator.py` — LLM-based strategy generation from episode patterns
- `memory/models.py` — episode data models
- `memory_service.py` — HTTP client for config-service memory API

## Agent Run Recording

Every investigation is recorded with full tool call traces:

- Run metadata (start time, prompt, agent config, status) stored in config-service
- Tool calls captured with input/output for each invocation
- TraceViewer in web UI shows expandable tool call timeline

## Key Files

- **agent.py** — `InteractiveAgentSession` — Claude Agent SDK engine
- **server_simple.py** — FastAPI server (port 8001), SSE streaming, simple mode entry point
- **investigation_runtime.py** — concurrency gate, run status registry, internal token check for simple mode
- **server.py** — full server (multi-tenant, config-service integration)
- **config.py** — config-service client, skills filtering logic
- **memory/** — episodic memory system (integration, strategy generator, models)
- **memory_service.py** — HTTP client for config-service memory/episode API
- **tools/** — Neo4j semantic layer
- **pyproject.toml** — Python dependencies
- **Dockerfile** — container image
- **.claude/skills/** — 22 skills with methodology docs and scripts

## Features

- **Claude Agent SDK** — direct Anthropic API, no proxy layer required
- **Skills + Scripts Architecture** — context-efficient integrations via Python scripts
- **Episodic Memory** — learn from past investigations, strategy generation
- **Skills Filtering** — per-agent skill access control
- **Agent Run Recording** — full tool call traces for observability
- **Neo4j Knowledge Graph** — service topology and blast radius analysis
- **Laminar Tracing** — full observability and debugging
