import type { CostEffectivenessReport, IssueList } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function getIssues(): Promise<IssueList> {
  try {
    const response = await fetch(`${API_URL}/api/v1/issues`, { cache: "no-store" });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return response.json();
  } catch {
    return {
      repository: "rohanmalige/GreenGauge",
      issues: [],
      total: 0,
      generated_at: new Date().toISOString(),
    };
  }
}

export async function getCostEffectiveness(): Promise<CostEffectivenessReport> {
  try {
    const response = await fetch(`${API_URL}/api/v1/metrics/cost-effectiveness`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return response.json();
  } catch {
    return {
      repository: "rohanmalige/GreenGauge",
      groups: [],
      generated_at: new Date().toISOString(),
    };
  }
}
