import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import Issue, Recommendation, SessionEvent, SessionRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    author TEXT NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '[]',
    html_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    repository TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    issue_number INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    FOREIGN KEY(issue_number) REFERENCES issues(number) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    repository TEXT,
    issue_number INTEGER,
    model TEXT,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    outcome TEXT
);

CREATE INDEX IF NOT EXISTS idx_issues_state_created_at ON issues(state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_events_session_id ON session_events(session_id, occurred_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA optimize")

    def upsert_issue(self, issue: Issue, repository: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO issues(id, number, title, body, state, author, labels_json, html_url, created_at, repository)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(number) DO UPDATE SET
                    title=excluded.title, body=excluded.body, state=excluded.state,
                    author=excluded.author, labels_json=excluded.labels_json,
                    html_url=excluded.html_url, repository=excluded.repository
                """,
                (issue.id, issue.number, issue.title, issue.body, issue.state, issue.author,
                 json.dumps(issue.labels), issue.html_url, issue.created_at.isoformat(), repository),
            )

    def save_recommendation(self, issue_number: int, recommendation: Recommendation) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO recommendations(issue_number, payload_json, generated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(issue_number) DO UPDATE SET
                    payload_json=excluded.payload_json, generated_at=excluded.generated_at
                """,
                (issue_number, recommendation.model_dump_json(), recommendation.generated_at.isoformat()),
            )

    def list_issues(self, state: str = "open") -> list[Issue]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*, r.payload_json AS recommendation_json
                FROM issues i LEFT JOIN recommendations r ON r.issue_number = i.number
                WHERE i.state = ? ORDER BY i.created_at DESC
                """,
                (state,),
            ).fetchall()
        return [self._issue_from_row(row) for row in rows]

    def get_issue(self, issue_number: int) -> Issue | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT i.*, r.payload_json AS recommendation_json
                FROM issues i LEFT JOIN recommendations r ON r.issue_number = i.number
                WHERE i.number = ?
                """,
                (issue_number,),
            ).fetchone()
        return self._issue_from_row(row) if row else None

    def add_session_event(self, event: SessionEvent) -> SessionRecord:
        received_at = datetime.now(timezone.utc)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO session_events(session_id, event_type, repository, issue_number, model,
                                           occurred_at, received_at, metrics_json, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event.session_id, event.event_type, event.repository, event.issue_number, event.model,
                 event.occurred_at.isoformat(), received_at.isoformat(), json.dumps(event.metrics), event.outcome),
            )
        return SessionRecord(**event.model_dump(), received_at=received_at)

    def list_session_events(self, limit: int = 100) -> list[SessionRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM session_events ORDER BY occurred_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [SessionRecord(
            session_id=row["session_id"], event_type=row["event_type"], repository=row["repository"],
            issue_number=row["issue_number"], model=row["model"], occurred_at=row["occurred_at"],
            received_at=row["received_at"], metrics=json.loads(row["metrics_json"]), outcome=row["outcome"],
        ) for row in rows]

    @staticmethod
    def _issue_from_row(row: sqlite3.Row) -> Issue:
        recommendation = Recommendation.model_validate_json(row["recommendation_json"]) if row["recommendation_json"] else None
        return Issue(
            id=row["id"], number=row["number"], title=row["title"], body=row["body"],
            state=row["state"], author=row["author"], labels=json.loads(row["labels_json"]),
            html_url=row["html_url"], created_at=row["created_at"], recommendation=recommendation,
        )

