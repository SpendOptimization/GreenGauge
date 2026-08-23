import { getIssues } from "@/lib/api";
import type { Issue, Recommendation } from "@/lib/types";
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

function IssueCard({ issue }: { issue: Issue }) {
  const recommendation = issue.recommendation;
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
            <p>{recommendation.reasoning}</p>
          </div>
        )}
      </div>

      <div className="recommendation-panel">
        <span className="eyebrow">Recommended route</span>
        <ModelBadge recommendation={recommendation} />
        {recommendation && (
          <>
            <div className="metric-grid">
              <div><strong>{Math.round(recommendation.confidence * 100)}%</strong><span>confidence</span></div>
              <div><strong>{formatMoney(recommendation.expected_cost_usd)}</strong><span>expected</span></div>
              <div><strong>{recommendation.expected_iterations}</strong><span>iterations</span></div>
            </div>
            <div className="similar-list">
              <span className="eyebrow">Closest prior work</span>
              {recommendation.similar_issues.slice(0, 2).map((similar) => (
                <a href={similar.url} target="_blank" rel="noreferrer" key={similar.number}>
                  <span>#{similar.number} · {similar.model_used}</span>
                  <strong>{formatMoney(similar.total_cost_usd)}</strong>
                </a>
              ))}
            </div>
          </>
        )}
      </div>
    </article>
  );
}

export default async function Home() {
  const data = await getIssues();
  const recommendations = data.issues.flatMap((issue) => issue.recommendation ? [issue.recommendation] : []);
  const projectedSpend = recommendations.reduce((sum, recommendation) => sum + recommendation.expected_cost_usd, 0);
  const premiumAvoided = recommendations.filter((recommendation) => recommendation.model_class !== "frontier").length;

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
          <p className="hero-copy">Pick the model that minimizes total cost—not just token price—before a developer starts the work.</p>
        </div>
        <div className="summary-card">
          <span className="eyebrow">Current queue</span>
          <div className="summary-row"><strong>{data.total}</strong><span>open issues</span></div>
          <div className="summary-row"><strong>{formatMoney(projectedSpend)}</strong><span>projected AI spend</span></div>
          <div className="summary-row accent"><strong>{premiumAvoided}</strong><span>premium routes avoided</span></div>
        </div>
      </section>

      <section className="queue-header">
        <div><h2>Recommendation queue</h2><p>Cached at issue creation · refreshes from the webhook</p></div>
        <div className="queue-actions">
          <SyncButton />
          <button type="button">All open issues <span>⌄</span></button>
        </div>
      </section>

      <section className="issue-list">
        {data.issues.length > 0 ? data.issues.map((issue) => <IssueCard issue={issue} key={issue.id} />) : (
          <div className="empty-state">
            <span>API offline</span>
            <h2>Start the GreenGauge API to load the seeded issue queue.</h2>
            <code>uv run --project apps/api uvicorn greengauge_api.main:app --reload</code>
          </div>
        )}
      </section>

      <footer><span>GreenGauge prototype</span><span>Recommendations are estimates · verify before routing production work</span></footer>
    </main>
  );
}
