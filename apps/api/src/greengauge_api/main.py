import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import Database
from .models import (
    CodingSessionFinish,
    CodingSessionStart,
    GitHubIssueEvent,
    GitHubSyncResult,
    Issue,
    IssueList,
    Recommendation,
    SessionEvent,
    SessionRecord,
    TelemetryAck,
    TurnTelemetry,
    WorkItemMetrics,
)
from .seed import seed_if_empty
from .services.categorization import classify_issue
from .services.github import GitHubClient
from .services.pricing import ModelPricingCatalog
from .services.recommendation import RecommendationEngine

settings = get_settings()
database = Database(settings.database_file)
engine = RecommendationEngine()
github_client = GitHubClient(settings.github_token)
pricing = ModelPricingCatalog.from_json(settings.model_pricing_json)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    seed_if_empty(database, engine, settings.github_repository)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def verify_mcp_key(authorization: str | None = Header(default=None)) -> None:
    if settings.mcp_api_key and authorization != f"Bearer {settings.mcp_api_key}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics API key")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/v1/issues", response_model=IssueList)
def list_issues(state_filter: str = Query(default="open", alias="state")) -> IssueList:
    issues = database.list_issues(state_filter)
    return IssueList(
        repository=settings.github_repository,
        issues=issues,
        total=len(issues),
        generated_at=datetime.now(timezone.utc),
    )


@app.get("/api/v1/issues/{issue_number}", response_model=Issue)
def get_issue(issue_number: int) -> Issue:
    issue = database.get_issue(issue_number)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


@app.post("/api/v1/github/sync", response_model=GitHubSyncResult)
async def sync_github_issues() -> GitHubSyncResult:
    try:
        github_issues = await github_client.list_open_issues(settings.github_repository)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"GitHub sync failed: {exc}") from exc

    recommended = 0
    current_numbers: list[int] = []
    for github_issue in github_issues:
        existing = database.get_issue(github_issue.number)
        issue = Issue(
            id=github_issue.id,
            number=github_issue.number,
            title=github_issue.title,
            body=github_issue.body or "",
            state=github_issue.state,
            author=github_issue.user.login,
            labels=[label.name for label in github_issue.labels],
            html_url=github_issue.html_url,
            created_at=github_issue.created_at,
            issue_type=classify_issue([label.name for label in github_issue.labels]),
        )
        database.upsert_issue(issue, settings.github_repository)
        current_numbers.append(issue.number)
        if not existing or not existing.recommendation:
            database.save_recommendation(issue.number, engine.recommend(issue))
            recommended += 1

    removed = database.prune_open_issues(settings.github_repository, current_numbers)
    return GitHubSyncResult(
        repository=settings.github_repository,
        imported=len(github_issues),
        recommended=recommended,
        removed=removed,
        synced_at=datetime.now(timezone.utc),
    )


@app.post("/api/v1/recommendations/{issue_number}/refresh", response_model=Recommendation)
def refresh_recommendation(issue_number: int) -> Recommendation:
    issue = database.get_issue(issue_number)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    recommendation = engine.recommend(issue)
    database.save_recommendation(issue_number, recommendation)
    return recommendation


@app.post("/api/v1/github/webhooks", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, str | int]:
    payload = await request.body()
    if settings.github_webhook_secret:
        expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event == "ping":
        return {"status": "pong", "issue_number": 0}
    if x_github_event and x_github_event != "issues":
        return {"status": "ignored", "issue_number": 0}

    event = GitHubIssueEvent.model_validate_json(payload)
    if event.action not in {"opened", "labeled", "unlabeled", "edited"}:
        return {"status": "ignored", "issue_number": event.issue.number}

    labels = [label.name for label in event.issue.labels]
    issue = Issue(
        id=event.issue.id, number=event.issue.number, title=event.issue.title, body=event.issue.body or "",
        state=event.issue.state, author=event.issue.user.login, labels=labels,
        html_url=event.issue.html_url, created_at=event.issue.created_at,
        issue_type=classify_issue(labels),
    )
    existing = database.get_issue(issue.number)
    database.upsert_issue(issue, event.repository.full_name)
    if event.action == "opened":
        if existing and existing.recommendation:
            return {"status": "cached", "issue_number": issue.number}
        database.save_recommendation(issue.number, engine.recommend(issue))
        return {"status": "recommended", "issue_number": issue.number}
    return {"status": "metadata_updated", "issue_number": issue.number}


@app.post(
    "/api/v1/telemetry/sessions/start",
    response_model=TelemetryAck,
    dependencies=[Depends(verify_mcp_key)],
)
def start_coding_session(event: CodingSessionStart) -> TelemetryAck:
    try:
        return database.start_coding_session(event)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/v1/telemetry/sessions/{session_id}/turns",
    response_model=TelemetryAck,
    dependencies=[Depends(verify_mcp_key)],
)
def record_turn_telemetry(session_id: str, event: TurnTelemetry) -> TelemetryAck:
    if event.session_id != session_id:
        raise HTTPException(status_code=422, detail="Path and payload session IDs differ")
    try:
        return database.record_turn(event, pricing)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/v1/telemetry/sessions/{session_id}/finish",
    response_model=TelemetryAck,
    dependencies=[Depends(verify_mcp_key)],
)
def finish_coding_session(session_id: str, event: CodingSessionFinish) -> TelemetryAck:
    if event.session_id != session_id:
        raise HTTPException(status_code=422, detail="Path and payload session IDs differ")
    try:
        return database.finish_coding_session(event, pricing)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/api/v1/metrics/work-items",
    response_model=list[WorkItemMetrics],
    dependencies=[Depends(verify_mcp_key)],
)
def list_work_item_metrics(repository: str | None = None) -> list[WorkItemMetrics]:
    return database.list_work_item_metrics(repository)


@app.get(
    "/api/v1/metrics/work-items/{issue_number}",
    response_model=WorkItemMetrics,
    dependencies=[Depends(verify_mcp_key)],
)
def get_work_item_metrics(
    issue_number: int,
    repository: str = Query(default=settings.github_repository),
) -> WorkItemMetrics:
    metrics = database.get_work_item_metrics(repository, issue_number)
    if not metrics:
        raise HTTPException(status_code=404, detail="Work item metrics not found")
    return metrics


@app.post("/api/v1/sessions/events", response_model=SessionRecord, dependencies=[Depends(verify_mcp_key)])
def record_session_event(event: SessionEvent) -> SessionRecord:
    return database.add_session_event(event)


@app.get("/api/v1/sessions", response_model=list[SessionRecord], dependencies=[Depends(verify_mcp_key)])
def list_session_events(limit: int = Query(default=100, ge=1, le=1000)) -> list[SessionRecord]:
    return database.list_session_events(limit)
