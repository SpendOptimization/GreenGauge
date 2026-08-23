"use client";

import { useState } from "react";
import type { CostEffectivenessGroup } from "@/lib/types";

function formatMoney(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value);
}

export function CostTable({ groups }: { groups: CostEffectivenessGroup[] }) {
  const [autonomousOnly, setAutonomousOnly] = useState(false);

  return (
    <section className="cost-section">
      <div className="cost-heading">
        <div>
          <p className="overline">COMPLETED-RUN ECONOMICS</p>
          <h2>Cost per green issue</h2>
          <p>Observed model spend, including failed attempts and retries. No human-time dollar estimate.</p>
        </div>
        <div className="completion-toggle" aria-label="Completion type">
          <button
            className={!autonomousOnly ? "active" : ""}
            type="button"
            onClick={() => setAutonomousOnly(false)}
          >
            All green
          </button>
          <button
            className={autonomousOnly ? "active" : ""}
            type="button"
            onClick={() => setAutonomousOnly(true)}
          >
            Autonomous only
          </button>
        </div>
      </div>

      {groups.length === 0 ? (
        <div className="cost-empty">
          <strong>No completed runs yet</strong>
          <span>Cost evidence will appear after a Codex run finishes or fails with recorded token usage.</span>
        </div>
      ) : (
        <div className="cost-table-wrap">
          <table className="cost-table">
            <thead>
              <tr>
                <th>Model / issue type</th>
                <th>Cost per green</th>
                <th>Interruptions / green</th>
                <th>Green / attempted</th>
                <th>Total model spend</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => {
                const green = autonomousOnly ? group.autonomous_green_issues : group.green_issues;
                const cpgi = autonomousOnly
                  ? group.autonomous_cost_per_green_issue_usd
                  : group.cost_per_green_issue_usd;
                const interruptionsPerGreen = green
                  ? Math.round((group.total_human_interruptions / green) * 1000) / 1000
                  : null;
                return (
                  <tr key={`${group.model}-${group.issue_type}`}>
                    <td><strong>{group.model}</strong><span>{group.issue_type}</span></td>
                    <td>
                      {cpgi === null
                        ? <><strong>N/A</strong><span>No green issues — {formatMoney(group.total_spend_usd)} spent</span></>
                        : <><strong>{formatMoney(cpgi)}</strong><span>observed CPGI</span></>}
                    </td>
                    <td>
                      <strong>{interruptionsPerGreen ?? "N/A"}</strong>
                      <span>{group.total_human_interruptions} total episodes</span>
                    </td>
                    <td><strong>{green} / {group.attempted_issues}</strong><span>completed attempts</span></td>
                    <td><strong>{formatMoney(group.total_spend_usd)}</strong><span>failures included</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="cost-footnote">Green requires both acceptance and regression tests to pass. Autonomous green also requires zero clarification episodes.</p>
    </section>
  );
}
