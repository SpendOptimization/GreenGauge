import { randomUUID } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

type Session = {
  sessionId: string;
  repository: string;
  issueNumber?: number;
  model?: string;
  startedAt: string;
  metrics: Record<string, unknown>;
};

const sessions = new Map<string, Session>();
const apiUrl = (process.env.GREENGAUGE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const apiKey = process.env.GREENGAUGE_MCP_API_KEY;

const server = new McpServer(
  { name: "greengauge-metrics", version: "0.1.0" },
  {
    instructions:
      "Use these tools to measure coding work for GreenGauge. Call start_coding_session once when beginning an issue, record_session_checkpoint after meaningful iterations, and finish_coding_session once after validation. Never include source code, secrets, prompts, or file contents in metrics; send counts, paths, timings, model names, and outcomes only.",
  },
);

async function sendEvent(payload: Record<string, unknown>) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (apiKey) headers.authorization = `Bearer ${apiKey}`;
  const response = await fetch(`${apiUrl}/api/v1/sessions/events`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`GreenGauge API returned ${response.status}: ${await response.text()}`);
  return response.json();
}

server.registerTool(
  "start_coding_session",
  {
    title: "Start coding session",
    description: "Start collecting aggregate metrics for work on a GitHub issue.",
    inputSchema: {
      repository: z.string().describe("Repository in owner/name form"),
      issueNumber: z.number().int().positive().optional(),
      model: z.string().optional().describe("Model or model class used for the work"),
      sessionId: z.string().optional().describe("Stable session ID; generated when omitted"),
    },
  },
  async ({ repository, issueNumber, model, sessionId }) => {
    const id = sessionId ?? randomUUID();
    const startedAt = new Date().toISOString();
    sessions.set(id, { sessionId: id, repository, issueNumber, model, startedAt, metrics: {} });
    await sendEvent({ session_id: id, event_type: "started", repository, issue_number: issueNumber, model, occurred_at: startedAt, metrics: {} });
    return { content: [{ type: "text", text: JSON.stringify({ sessionId: id, status: "started" }) }] };
  },
);

server.registerTool(
  "record_session_checkpoint",
  {
    title: "Record session checkpoint",
    description: "Merge aggregate, non-sensitive counters and progress metrics into an active coding session.",
    inputSchema: {
      sessionId: z.string(),
      iteration: z.number().int().nonnegative().optional(),
      toolCalls: z.number().int().nonnegative().optional(),
      filesTouched: z.array(z.string()).optional().describe("Repository-relative file paths only"),
      inputTokens: z.number().int().nonnegative().optional(),
      cachedInputTokens: z.number().int().nonnegative().optional(),
      outputTokens: z.number().int().nonnegative().optional(),
      humanInterventions: z.number().int().nonnegative().optional(),
      extraMetrics: z.record(z.unknown()).optional().describe("Temporary extension point for future metrics"),
    },
  },
  async ({ sessionId, extraMetrics, ...checkpoint }) => {
    const session = sessions.get(sessionId);
    if (!session) throw new Error(`Unknown session: ${sessionId}`);
    const metrics = { ...session.metrics, ...checkpoint, ...extraMetrics };
    session.metrics = metrics;
    const occurredAt = new Date().toISOString();
    await sendEvent({
      session_id: sessionId, event_type: "checkpoint", repository: session.repository,
      issue_number: session.issueNumber, model: session.model, occurred_at: occurredAt, metrics,
    });
    return { content: [{ type: "text", text: JSON.stringify({ sessionId, status: "checkpoint_recorded", metrics }) }] };
  },
);

server.registerTool(
  "finish_coding_session",
  {
    title: "Finish coding session",
    description: "Finalize and submit accumulated coding-session metrics to GreenGauge.",
    inputSchema: {
      sessionId: z.string(),
      outcome: z.enum(["success", "partial", "failed", "abandoned"]),
      durationSeconds: z.number().nonnegative().optional(),
      iterations: z.number().int().nonnegative().optional(),
      testsPassed: z.boolean().optional(),
      pullRequestUrl: z.string().url().optional(),
      finalMetrics: z.record(z.unknown()).optional(),
    },
  },
  async ({ sessionId, outcome, finalMetrics, ...finalFields }) => {
    const session = sessions.get(sessionId);
    if (!session) throw new Error(`Unknown session: ${sessionId}`);
    const metrics = { ...session.metrics, ...finalFields, ...finalMetrics };
    const occurredAt = new Date().toISOString();
    await sendEvent({
      session_id: sessionId, event_type: "finished", repository: session.repository,
      issue_number: session.issueNumber, model: session.model, occurred_at: occurredAt, metrics, outcome,
    });
    sessions.delete(sessionId);
    return { content: [{ type: "text", text: JSON.stringify({ sessionId, status: "finished", outcome, metrics }) }] };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
