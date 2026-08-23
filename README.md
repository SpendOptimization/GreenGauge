# GreenGauge

GreenGauge recommends the lowest-cost coding model likely to finish a GitHub issue successfully. This hackathon foundation includes:

- a Next.js issue dashboard;
- a FastAPI service with a local SQLite cache;
- a GitHub issue-opened and label-change webhook flow;
- project-level Codex lifecycle hooks plus an MCP server for per-turn metrics;
- a replaceable recommendation-engine boundary, currently backed by a deterministic placeholder.

## Architecture

```text
GitHub issue opened ──webhook──▶ FastAPI ──▶ recommendation engine
                                         └──▶ SQLite cache

Codex lifecycle hooks ──every turn──▶ FastAPI ──▶ session/work-item aggregates
             Codex MCP ──rich deltas──┘

Next.js dashboard ──GET /api/v1/issues──▶ FastAPI/SQLite
```

The dashboard reads cached recommendations; it never runs recommendation inference during page load.

## Run locally

Requirements: Node 20+, Python 3.9+, and `uv`.

```bash
cp .env.example .env
npm install
uv sync --project apps/api
```

Start the API:

```bash
uv run --project apps/api uvicorn greengauge_api.main:app --reload --port 8000
```

Start the dashboard in another terminal:

```bash
npm run dev:web
```

Open [http://localhost:3000](http://localhost:3000). The API seeds a few representative issues on its first run.

## Connect GitHub issues

For the hackathon, use one repository token instead of building GitHub App OAuth.

1. Set `GITHUB_REPOSITORY=owner/repo`. For a private repository, also set a fine-grained `GITHUB_TOKEN` with read-only Issues and Metadata access.
2. Start the API and expose port 8000 with `ngrok http 8000`.
3. In the repository's **Settings → Webhooks**, create a webhook pointing to `https://<ngrok-host>/api/v1/github/webhooks`.
4. Choose `application/json`, set the same secret in GitHub and `GITHUB_WEBHOOK_SECRET`, and subscribe only to **Issues** events.

When an issue is opened, the API saves it, generates a placeholder recommendation once, and caches both records in SQLite. The same Issues webhook receives `labeled` and `unlabeled` actions; those refresh the cached issue category without rerunning recommendation inference. The engine boundary in `apps/api/src/greengauge_api/services/recommendation.py` is where the similarity lookup and small-model call belong later.

Use **Sync GitHub** on the dashboard once to import the repository's existing open issues. Public repositories work without a token; a token is recommended for private repositories and to avoid GitHub's low anonymous rate limit. Syncing generates recommendations only for newly seen issues and removes demo/stale open issues from the local cache.

## Connect Codex metrics

The project-scoped `.codex/config.toml` registers the STDIO MCP server, while `.codex/hooks.json` provides deterministic lifecycle capture. After installing dependencies and starting the API, restart Codex in this repository, run `/hooks`, and approve the project hooks once. Codex then runs these automatically:

- `SessionStart` creates or resumes a session record.
- `UserPromptSubmit` identifies the current turn and counts prompts after the first as a human-intervention proxy.
- `PostToolUse` records recognized test/CI command attempts and outcomes.
- `Stop` sends one idempotent delta containing active working time, test counters, changed paths/modules, change type, and logic-branch deltas.
- `SessionEnd` closes the session.

The MCP tools complement the hook data:

- `attach_coding_session` links the Codex session to an issue and/or PR.
- `record_turn_metrics` sends semantic and runtime-provided token/model deltas before every final response.
- `finish_coding_session` records a known outcome; `SessionEnd` remains the fallback.

All numeric payloads are deltas for one turn, never cumulative totals. Event IDs make retries safe, and a turn reported by both the hook and MCP increments `turn_count` only once. No prompt, response, command, tool output, source content, or secret is sent. If the API is unavailable, the hook queues delivery under `.git/greengauge-telemetry/` and does not block Codex.

SQLite maintains one `work_item_metrics` row per issue (or standalone PR), any number of `coding_sessions`, and raw idempotent `telemetry_events`. This lets one PR aggregate multiple Codex sessions while preserving `session_count`.

Token usage is recorded only when the runtime exposes exact counters to the agent; the hook contract itself does not provide stable token counts, so the collector deliberately does not estimate them. Configure placeholder prices in `GREENGAUGE_MODEL_PRICING_JSON`; unknown models cost `$0` until a rate or per-event override is supplied.

You can verify the server independently with:

```bash
npm --workspace @greengauge/codex-mcp run build
```

## API shortcuts

- `GET /health`
- `GET /api/v1/issues`
- `GET /api/v1/issues/{issue_number}`
- `POST /api/v1/github/webhooks`
- `POST /api/v1/github/sync`
- `POST /api/v1/recommendations/{issue_number}/refresh`
- `POST /api/v1/telemetry/sessions/start`
- `POST /api/v1/telemetry/sessions/{session_id}/turns`
- `POST /api/v1/telemetry/sessions/{session_id}/finish`
- `GET /api/v1/metrics/work-items`
- `GET /api/v1/metrics/work-items/{issue_number}`
- `POST /api/v1/sessions/events`
- `GET /api/v1/sessions`

Interactive API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

## Deployment shape

Deploy `apps/web` as one Vercel project and `apps/api` as a second Python project. SQLite is intentionally local-only for this three-hour prototype; before production deployment, swap the small repository module for Postgres, Neon, Turso, or another persistent hosted database. No dashboard code needs to change.
