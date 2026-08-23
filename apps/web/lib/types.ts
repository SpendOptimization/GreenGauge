export type SimilarIssue = {
  number: number;
  title: string;
  model_used: string;
  total_cost_usd: number;
  similarity: number;
  url: string;
};

export type Recommendation = {
  model: string;
  model_class: string;
  confidence: number;
  expected_cost_usd: number;
  expected_iterations: number;
  reasoning: string;
  status: string;
  generated_at: string;
  similar_issues: SimilarIssue[];
};

export type Issue = {
  id: number;
  number: number;
  title: string;
  body: string;
  state: string;
  author: string;
  labels: string[];
  html_url: string;
  created_at: string;
  issue_type: string;
  recommendation: Recommendation | null;
};

export type CostEffectivenessGroup = {
  model: string;
  issue_type_counts: Record<string, number>;
  attempted_issues: number;
  green_issues: number;
  autonomous_green_issues: number;
  total_spend_usd: number;
  cost_per_green_issue_usd: number | null;
  autonomous_cost_per_green_issue_usd: number | null;
  total_human_interruptions: number;
  interruptions_per_green_issue: number | null;
};

export type CostEffectivenessReport = {
  repository: string;
  groups: CostEffectivenessGroup[];
  generated_at: string;
};

export type IssueList = {
  repository: string;
  issues: Issue[];
  total: number;
  generated_at: string;
};
