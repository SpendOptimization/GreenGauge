import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const apiUrl = (process.env.GREENGAUGE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const apiKey = process.env.GREENGAUGE_MCP_API_KEY;

const server = new McpServer(
  { name: "greengauge-metrics", version: "0.2.0" },
  {
    instructions: [
      "GreenGauge's project hooks automatically create a baseline coding session and report observable metrics at every Codex turn stop.",
      "The hook context gives you the exact GreenGauge sessionId and turnId for the current turn.",
      "Once the GitHub issue or PR is known, call attach_coding_session with that same sessionId so every session rolls up to one work item.",
      "Before the final response of EVERY turn, call record_turn_metrics once with that same sessionId and turnId.",
      "Every numeric value must be an incremental delta for only this turn, never a session-to-date total.",
      "Report only runtime-provided token counts; never estimate them. Group token deltas by exact model name.",
      "Prefer semantic fields the hook cannot know (changeTypes and extension metrics). Do not repeat hook-observed active time, test attempts, human interventions, or file paths unless the hook context says baseline reporting failed.",
      "Never send source code, file contents, prompts, assistant messages, secrets, or personal data.",
    ].join(" "),
  },
);

async function send(path: string, payload: Record<string, unknown>) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (apiKey) headers.authorization = `Bearer ${apiKey}`;
  const response = await fetch(`${apiUrl}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`GreenGauge API returned ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

const modelUsageSchema = z.object({
  model: z.string().describe("Exact model name reported by the runtime"),
  inputTokens: z.number().int().nonnegative().default(0),
  cachedInputTokens: z.number().int().nonnegative().default(0),
  outputTokens: z.number().int().nonnegative().default(0),
  reasoningTokens: z.number().int().nonnegative().default(0),
  inputCostPerMillion: z.number().nonnegative().optional(),
  cachedInputCostPerMillion: z.number().nonnegative().optional(),
  outputCostPerMillion: z.number().nonnegative().optional(),
});

server.registerTool(
  "attach_coding_session",
  {
    title: "Attach coding session to GitHub work",
    description:
      "Attach the hook-created Codex session to an issue or PR. Safe to call again when PR details become known.",
    inputSchema: {
      sessionId: z.string().describe("Exact sessionId injected by the GreenGauge hook"),
      repository: z.string().describe("Repository in owner/name form"),
      issueNumber: z.number().int().positive().optional(),
      pullRequestNumber: z.number().int().positive().optional(),
      pullRequestUrl: z.string().url().optional(),
      model: z.string().optional(),
      branch: z.string().optional(),
      startedAt: z.string().datetime().optional(),
    },
  },
  async ({ sessionId, repository, issueNumber, pullRequestNumber, pullRequestUrl, model, branch, startedAt }) => {
    const result = await send("/api/v1/telemetry/sessions/start", {
      session_id: sessionId,
      repository,
      issue_number: issueNumber,
      pr_number: pullRequestNumber,
      pr_url: pullRequestUrl,
      model,
      branch,
      started_at: startedAt ?? new Date().toISOString(),
      source: "mcp",
    });
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  },
);

server.registerTool(
  "record_turn_metrics",
  {
    title: "Record incremental turn metrics",
    description:
      "Record delta-only metrics for exactly one Codex turn. Call once before the final response of every turn.",
    inputSchema: {
      sessionId: z.string().describe("Exact sessionId injected by the GreenGauge hook"),
      turnId: z.string().describe("Exact turnId injected by the GreenGauge hook"),
      eventId: z.string().optional().describe("Optional idempotency key; generated deterministically if omitted"),
      modelUsage: z.array(modelUsageSchema).default([]),
      changeTypes: z
        .array(z.enum(["frontend", "backend", "async", "cdc", "database", "infra", "tests", "docs", "other"]))
        .default([]),
      modulesTouched: z.array(z.string()).default([]),
      logicBranchesAdded: z.number().int().nonnegative().default(0),
      logicBranchesRemoved: z.number().int().nonnegative().default(0),
      prThreadsMultiParticipant: z.number().int().nonnegative().default(0),
      humanInterventions: z.number().int().nonnegative().default(0),
      ciAttempts: z.number().int().nonnegative().default(0),
      ciFirstTrySuccesses: z.number().int().nonnegative().default(0),
      ciSuccesses: z.number().int().nonnegative().default(0),
      ciFailures: z.number().int().nonnegative().default(0),
      allCiPassed: z.boolean().default(false),
      activeSeconds: z.number().nonnegative().default(0),
      filesTouched: z.array(z.string()).default([]),
      extraMetrics: z.record(z.unknown()).default({}),
    },
  },
  async ({
    sessionId, turnId, eventId, modelUsage, changeTypes, modulesTouched, logicBranchesAdded,
    logicBranchesRemoved, prThreadsMultiParticipant, humanInterventions, ciAttempts,
    ciFirstTrySuccesses, ciSuccesses, ciFailures, allCiPassed, activeSeconds, filesTouched,
    extraMetrics,
  }) => {
    const result = await send(`/api/v1/telemetry/sessions/${encodeURIComponent(sessionId)}/turns`, {
      event_id: eventId ?? `mcp-turn:${sessionId}:${turnId}`,
      session_id: sessionId,
      turn_id: turnId,
      occurred_at: new Date().toISOString(),
      source: "mcp",
      metrics: {
        human_interventions: humanInterventions,
        ci_attempts: ciAttempts,
        ci_first_try_successes: ciFirstTrySuccesses,
        ci_successes: ciSuccesses,
        ci_failures: ciFailures,
        all_ci_passed: allCiPassed,
        active_seconds: activeSeconds,
        logic_branches_added: logicBranchesAdded,
        logic_branches_removed: logicBranchesRemoved,
        pr_threads_multi_participant: prThreadsMultiParticipant,
        files_touched: filesTouched,
        modules_touched: modulesTouched,
        change_types: changeTypes,
        model_usage: modelUsage.map((usage) => ({
          model: usage.model,
          input_tokens: usage.inputTokens,
          cached_input_tokens: usage.cachedInputTokens,
          output_tokens: usage.outputTokens,
          reasoning_tokens: usage.reasoningTokens,
          input_cost_per_million: usage.inputCostPerMillion,
          cached_input_cost_per_million: usage.cachedInputCostPerMillion,
          output_cost_per_million: usage.outputCostPerMillion,
        })),
        extra: extraMetrics,
      },
    });
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  },
);

server.registerTool(
  "finish_coding_session",
  {
    title: "Finish coding session",
    description: "Mark a Codex coding session complete. SessionEnd hooks also do this as a fallback.",
    inputSchema: {
      sessionId: z.string(),
      outcome: z.enum(["success", "partial", "failed", "abandoned", "unknown"]),
      eventId: z.string().optional(),
      finalExtraMetrics: z.record(z.unknown()).default({}),
    },
  },
  async ({ sessionId, outcome, eventId, finalExtraMetrics }) => {
    const result = await send(`/api/v1/telemetry/sessions/${encodeURIComponent(sessionId)}/finish`, {
      event_id: eventId ?? `mcp-finish:${sessionId}`,
      session_id: sessionId,
      finished_at: new Date().toISOString(),
      outcome,
      source: "mcp",
      final_metrics: { extra: finalExtraMetrics },
    });
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
