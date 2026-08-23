from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SimilarIssue(BaseModel):
    number: int
    title: str
    model_used: str
    total_cost_usd: float
    similarity: float
    url: str


class Recommendation(BaseModel):
    model: str
    model_class: Literal["terra", "balanced", "frontier"]
    confidence: float = Field(ge=0, le=1)
    expected_cost_usd: float = Field(ge=0)
    expected_iterations: int = Field(ge=1)
    reasoning: str
    status: str = "ready"
    generated_at: datetime
    similar_issues: list[SimilarIssue] = Field(default_factory=list)


class Issue(BaseModel):
    id: int
    number: int
    title: str
    body: str = ""
    state: str = "open"
    author: str
    labels: list[str] = Field(default_factory=list)
    html_url: str
    created_at: datetime
    issue_type: str = "uncategorized"
    recommendation: Recommendation | None = None


class IssueList(BaseModel):
    repository: str
    issues: list[Issue]
    total: int
    generated_at: datetime


class GitHubSyncResult(BaseModel):
    repository: str
    imported: int
    recommended: int
    removed: int
    synced_at: datetime


class GitHubUser(BaseModel):
    login: str


class GitHubLabel(BaseModel):
    name: str


class GitHubIssue(BaseModel):
    id: int
    number: int
    title: str
    body: str | None = None
    state: str
    user: GitHubUser
    labels: list[GitHubLabel] = Field(default_factory=list)
    html_url: str
    created_at: datetime


class GitHubRepository(BaseModel):
    full_name: str


class GitHubIssueEvent(BaseModel):
    action: str
    issue: GitHubIssue
    repository: GitHubRepository


class SessionEvent(BaseModel):
    session_id: str
    event_type: Literal["started", "checkpoint", "finished"]
    repository: str | None = None
    issue_number: int | None = None
    model: str | None = None
    occurred_at: datetime
    metrics: dict[str, Any] = Field(default_factory=dict)
    outcome: str | None = None


class SessionRecord(SessionEvent):
    received_at: datetime


class ModelUsageDelta(BaseModel):
    call_id: str | None = None
    model: str
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    cached_input_cost_per_million: float | None = Field(default=None, ge=0)
    cache_write_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)


class TurnMetricDelta(BaseModel):
    human_interventions: int = Field(default=0, ge=0)
    human_clarification_episode_ids: list[str] = Field(default_factory=list)
    ci_attempts: int = Field(default=0, ge=0)
    ci_first_try_successes: int = Field(default=0, ge=0)
    ci_successes: int = Field(default=0, ge=0)
    ci_failures: int = Field(default=0, ge=0)
    all_ci_passed: bool = False
    acceptance_tests_passed: bool | None = None
    regression_tests_passed: bool | None = None
    active_seconds: float = Field(default=0, ge=0)
    logic_branches_added: int = Field(default=0, ge=0)
    logic_branches_removed: int = Field(default=0, ge=0)
    pr_threads_multi_participant: int = Field(default=0, ge=0)
    files_touched: list[str] = Field(default_factory=list)
    modules_touched: list[str] = Field(default_factory=list)
    change_types: list[str] = Field(default_factory=list)
    model_usage: list[ModelUsageDelta] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class CodingSessionStart(BaseModel):
    session_id: str
    repository: str
    issue_number: int | None = Field(default=None, ge=1)
    pr_number: int | None = Field(default=None, ge=1)
    pr_url: str | None = None
    model: str | None = None
    branch: str | None = None
    started_at: datetime
    source: str = "mcp"


class TurnTelemetry(BaseModel):
    event_id: str
    session_id: str
    turn_id: str
    occurred_at: datetime
    source: str = "mcp"
    metrics: TurnMetricDelta = Field(default_factory=TurnMetricDelta)


class CodingSessionFinish(BaseModel):
    event_id: str
    session_id: str
    finished_at: datetime
    outcome: Literal["success", "partial", "failed", "abandoned", "unknown"] = "unknown"
    termination_reason: Literal[
        "green", "gave_up", "turn_limit", "time_limit", "cost_limit", "abandoned", "unknown"
    ] = "unknown"
    source: str = "mcp"
    final_metrics: TurnMetricDelta = Field(default_factory=TurnMetricDelta)


class WorkItemMetrics(BaseModel):
    repository: str
    issue_number: int | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    issue_type: str = "uncategorized"
    labels: list[str] = Field(default_factory=list)
    session_count: int = 0
    turn_count: int = 0
    human_interventions: int = 0
    human_clarification_episode_ids: list[str] = Field(default_factory=list)
    ci_attempts: int = 0
    ci_first_try_successes: int = 0
    ci_successes: int = 0
    ci_failures: int = 0
    active_seconds: float = 0
    logic_branches_added: int = 0
    logic_branches_removed: int = 0
    pr_threads_multi_participant: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_cost_usd: float = 0
    acceptance_tests_passed: bool = False
    regression_tests_passed: bool = False
    green: bool = False
    autonomous_green: bool = False
    termination_reason: str | None = None
    model_usage: dict[str, dict[str, int | float]] = Field(default_factory=dict)
    files_touched: list[str] = Field(default_factory=list)
    modules_touched: list[str] = Field(default_factory=list)
    change_types: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    first_started_at: datetime | None = None
    ci_passing_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class TelemetryAck(BaseModel):
    accepted: bool
    duplicate: bool = False
    aggregate: WorkItemMetrics | None = None


class CostEffectivenessGroup(BaseModel):
    model: str
    issue_type_counts: dict[str, int] = Field(default_factory=dict)
    attempted_issues: int
    green_issues: int
    autonomous_green_issues: int
    total_spend_usd: float
    cost_per_green_issue_usd: float | None = None
    autonomous_cost_per_green_issue_usd: float | None = None
    total_human_interruptions: int
    interruptions_per_green_issue: float | None = None


class CostEffectivenessReport(BaseModel):
    repository: str
    groups: list[CostEffectivenessGroup]
    generated_at: datetime
