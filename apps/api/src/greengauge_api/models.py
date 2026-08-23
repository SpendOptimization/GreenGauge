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
    similar_issues: list[SimilarIssue] = []


class Issue(BaseModel):
    id: int
    number: int
    title: str
    body: str = ""
    state: str = "open"
    author: str
    labels: list[str] = []
    html_url: str
    created_at: datetime
    recommendation: Recommendation | None = None


class IssueList(BaseModel):
    repository: str
    issues: list[Issue]
    total: int
    generated_at: datetime


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
    labels: list[GitHubLabel] = []
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
    metrics: dict[str, Any] = {}
    outcome: str | None = None


class SessionRecord(SessionEvent):
    received_at: datetime

