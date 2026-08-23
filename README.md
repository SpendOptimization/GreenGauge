# GreenGauge

GreenGauge recommends the lowest-cost coding model likely to finish a GitHub issue successfully. This hackathon foundation includes:

- a Next.js issue dashboard;
- a FastAPI service with a local SQLite cache;
- a GitHub `issues.opened` webhook flow;
- a Codex-compatible MCP server for recording coding-session metrics;
- a replaceable recommendation-engine boundary, currently backed by a deterministic placeholder.

## Architecture

```text
GitHub issue opened ──webhook──▶ FastAPI ──▶ recommendation engine
                                         └──▶ SQLite cache

Codex session ──MCP tools──▶ metrics collector ──HTTP──▶ FastAPI

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

When an issue is opened, the API saves it, generates a placeholder recommendation once, and caches both records in SQLite. The engine boundary in `apps/api/src/greengauge_api/services/recommendation.py` is where the similarity lookup and small-model call belong later.

Use **Sync GitHub** on the dashboard once to import the repository's existing open issues. Public repositories work without a token; a token is recommended for private repositories and to avoid GitHub's low anonymous rate limit. Syncing generates recommendations only for newly seen issues and removes demo/stale open issues from the local cache.

## Connect Codex metrics

The committed project-scoped `.codex/config.toml` registers the local STDIO MCP server. Install dependencies, trust/open the repository in Codex, start the API, and restart Codex. The MCP server exposes:

- `start_coding_session`
- `record_session_checkpoint`
- `finish_coding_session`

Codex can call these tools at the start, during, and at the end of issue work. The payload deliberately keeps `metrics` extensible until the final metric list is known. The final tool submits the accumulated session to the API.

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
- `POST /api/v1/sessions/events`
- `GET /api/v1/sessions`

Interactive API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

## Deployment shape

Deploy `apps/web` as one Vercel project and `apps/api` as a second Python project. SQLite is intentionally local-only for this three-hour prototype; before production deployment, swap the small repository module for Postgres, Neon, Turso, or another persistent hosted database. No dashboard code needs to change.
