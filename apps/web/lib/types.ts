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
  recommendation: Recommendation | null;
};

export type IssueList = {
  repository: string;
  issues: Issue[];
  total: number;
  generated_at: string;
};

