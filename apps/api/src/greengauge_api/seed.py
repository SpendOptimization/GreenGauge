from datetime import datetime, timedelta, timezone

from .db import Database
from .models import Issue
from .services.recommendation import RecommendationEngine


def seed_if_empty(database: Database, engine: RecommendationEngine, repository: str) -> None:
    if database.list_issues():
        return
    now = datetime.now(timezone.utc)
    issues = [
        Issue(id=101, number=84, title="Add usage-based billing export", body="Expose monthly token and human-time costs as a downloadable CSV for finance.", author="maya-chen", labels=["backend", "data"], html_url="https://github.com/rohanmalige/GreenGauge/issues/84", created_at=now - timedelta(hours=3)),
        Issue(id=102, number=83, title="Fix race condition in webhook processing", body="Duplicate GitHub deliveries can generate competing recommendations for the same issue.", author="devon-r", labels=["bug", "webhook"], html_url="https://github.com/rohanmalige/GreenGauge/issues/83", created_at=now - timedelta(days=1)),
        Issue(id=103, number=81, title="Polish empty state on repository dashboard", body="Add clearer setup copy and a retry action when the selected repository has no imported issues.", author="sloane", labels=["frontend", "good first issue"], html_url="https://github.com/rohanmalige/GreenGauge/issues/81", created_at=now - timedelta(days=2)),
    ]
    for issue in issues:
        database.upsert_issue(issue, repository)
        database.save_recommendation(issue.number, engine.recommend(issue))

