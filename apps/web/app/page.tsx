import { getCostEffectiveness, getIssues } from "@/lib/api";
import type { CostEffectivenessGroup, Issue, Recommendation } from "@/lib/types";
import { CostTable } from "./cost-table";
import { SyncButton } from "./sync-button";

function formatMoney(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);
}

function ModelBadge({ recommendation }: { recommendation: Recommendation | null }) {
  if (!recommendation) return <span className="model-badge pending">Analyzing</span>;
  return (
    <span className={`model-badge ${recommendation.model_class}`}>
      <span className="status-dot" />
      {recommendation.model}
    </span>
  );
}

function IssueCard({ issue, costGroups }: { issue: Issue; costGroups: CostEffectivenessGroup[] }) {
  const recommendation = issue.recommendation;
  const evidence = recommendation
    ? costGroups.find((group) =>
        group.model.toLowerCase() === recommendation.model.toLowerCase()
        && group.issue_type === issue.issue_type)
    : undefined;
  return (
    <article className="issue-card">
      <div className="issue-main">
        <div className="issue-kicker">
          <span>#{issue.number}</span>
          <span className="separator">·</span>
          <span>{new Date(issue.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span>
          {issue.labels.map((label) => <span className="label" key={label}>{label}</span>)}
        </div>
        <a className="issue-title" href={issue.html_url} target="_blank" rel="noreferrer">{issue.title}</a>
        <p className="issue-body">{issue.body || "No issue description provided."}</p>
        {recommendation && (
          <div className="reasoning">
            <span className="spark">✦</span>
            <p>{evidence
              ? recommendation.reasoning
              : "Preliminary complexity route only. No completed cost evidence exists for this model and issue type yet."}</p>
          </div>
        )}
      </div>

      <div className="recommendation-panel">
        <span className="eyebrow">Recommended route</span>
        <ModelBadge recommendation={recommendation} />
        {recommendation && (
          <>
            <div className="metric-grid">
              <div><strong>{evidence ? "Evidence" : "Heuristic"}</strong><span>routing basis</span></div>
              <div>
                <strong>{evidence?.cost_per_green_issue_usd == null ? "N/A" : formatMoney(evidence.cost_per_green_issue_usd)}</strong>
                <span>historical CPGI</span>
              </div>
              <div><strong>{evidence ? `${evidence.green_issues}/${evidence.attempted_issues}` : "0/0"}</strong><span>green / attempted</span></div>
            </div>
            {!evidence && <p className="evidence-note">No completed {issue.issue_type} runs for this model yet.</p>}
          </>
        )}
      </div>
    </article>
  );
}

export default async function Home() {
  const [data, costReport] = await Promise.all([getIssues(), getCostEffectiveness()]);
  const historicalSpend = costReport.groups.reduce((sum, group) => sum + group.total_spend_usd, 0);
  const attempted = costReport.groups.reduce((sum, group) => sum + group.attempted_issues, 0);
  const green = costReport.groups.reduce((sum, group) => sum + group.green_issues, 0);

  return (
    <main>
      <header className="topbar">
        <div className="brand"><span className="brand-mark">G</span><span>GreenGauge</span></div>
        <div className="repo-pill"><span className="github-mark">◉</span>{data.repository}<span className="chevron">⌄</span></div>
        <div className="live"><span />Webhook active</div>
      </header>

      <section className="hero">
        <div>
          <p className="overline">MODEL ROUTING / ISSUE QUEUE</p>
          <h1>Spend intelligence<br />for every issue.</h1>
          <p className="hero-copy">Route new work using observed cost per successful issue—not a speculative per-ticket dollar forecast.</p>
        </div>
        <div className="summary-card">
          <span className="eyebrow">Current queue</span>
          <div className="summary-row"><strong>{data.total}</strong><span>open issues</span></div>
          <div className="summary-row"><strong>{formatMoney(historicalSpend)}</strong><span>completed-run spend</span></div>
          <div className="summary-row accent"><strong>{green} / {attempted}</strong><span>green / attempted</span></div>
        </div>
      </section>

      <CostTable groups={costReport.groups} />

      <section className="queue-header">
        <div><h2>Recommendation queue</h2><p>Cached at issue creation · refreshes from the webhook</p></div>
        <div className="queue-actions">
          <SyncButton />
          <button type="button">All open issues <span>⌄</span></button>
        </div>
      </section>

      <section className="issue-list">
        {data.issues.length > 0 ? data.issues.map((issue) => (
          <IssueCard issue={issue} costGroups={costReport.groups} key={issue.id} />
        )) : (
          <div className="empty-state">
            <span>API offline</span>
            <h2>Start the GreenGauge API to load the seeded issue queue.</h2>
            <code>uv run --project apps/api uvicorn greengauge_api.main:app --reload</code>
          </div>
        )}
      </section>

      <footer><span>GreenGauge prototype</span><span>Cost metrics use completed runs only · no projected dollar cost</span></footer>
    </main>
  );
}
