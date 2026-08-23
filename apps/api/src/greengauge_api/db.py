import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import (
    CodingSessionFinish,
    CodingSessionStart,
    CostEffectivenessGroup,
    Issue,
    IssueAnalysis,
    Recommendation,
    SessionEvent,
    SessionRecord,
    TelemetryAck,
    TurnMetricDelta,
    TurnTelemetry,
    WorkItemMetrics,
)
from .services.categorization import classify_issue
from .services.pricing import ModelPricingCatalog


SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    author TEXT NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '[]',
    issue_type TEXT NOT NULL DEFAULT 'uncategorized',
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

CREATE TABLE IF NOT EXISTS issue_analysis (
    issue_number INTEGER PRIMARY KEY,
    repository TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL DEFAULT '',
    github_labels_json TEXT NOT NULL DEFAULT '[]',
    actionable_labels_json TEXT NOT NULL DEFAULT '[]',
    routing_labels_json TEXT NOT NULL DEFAULT '[]',
    disposition_label TEXT,
    benchmark_eligible INTEGER NOT NULL DEFAULT 0,
    issue_text TEXT NOT NULL,
    embedding_json TEXT,
    embedding_model TEXT,
    embedding_model_version TEXT,
    scope_score INTEGER NOT NULL,
    solution_uncertainty_score INTEGER NOT NULL,
    public_interface_score INTEGER NOT NULL,
    risk_compatibility_score INTEGER NOT NULL,
    testing_burden_score INTEGER NOT NULL,
    complexity_score INTEGER NOT NULL,
    complexity_class TEXT NOT NULL,
    classifier_model TEXT NOT NULL,
    classifier_prompt_version TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    classifier_confidence REAL NOT NULL,
    classification_evidence_json TEXT NOT NULL DEFAULT '{}',
    analyzed_at TEXT NOT NULL,
    FOREIGN KEY(issue_number) REFERENCES issues(number) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS work_item_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_key TEXT NOT NULL UNIQUE,
    repository TEXT NOT NULL,
    issue_number INTEGER,
    pr_number INTEGER,
    pr_url TEXT,
    issue_type TEXT NOT NULL DEFAULT 'uncategorized',
    labels_json TEXT NOT NULL DEFAULT '[]',
    session_count INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    human_interventions INTEGER NOT NULL DEFAULT 0,
    human_episode_ids_json TEXT NOT NULL DEFAULT '[]',
    ci_attempts INTEGER NOT NULL DEFAULT 0,
    ci_first_try_successes INTEGER NOT NULL DEFAULT 0,
    ci_successes INTEGER NOT NULL DEFAULT 0,
    ci_failures INTEGER NOT NULL DEFAULT 0,
    active_seconds REAL NOT NULL DEFAULT 0,
    logic_branches_added INTEGER NOT NULL DEFAULT 0,
    logic_branches_removed INTEGER NOT NULL DEFAULT 0,
    pr_threads_multi_participant INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    acceptance_tests_passed INTEGER NOT NULL DEFAULT 0,
    regression_tests_passed INTEGER NOT NULL DEFAULT 0,
    green INTEGER NOT NULL DEFAULT 0,
    autonomous_green INTEGER NOT NULL DEFAULT 0,
    termination_reason TEXT,
    model_usage_json TEXT NOT NULL DEFAULT '{}',
    files_touched_json TEXT NOT NULL DEFAULT '[]',
    modules_touched_json TEXT NOT NULL DEFAULT '[]',
    change_types_json TEXT NOT NULL DEFAULT '[]',
    extra_json TEXT NOT NULL DEFAULT '{}',
    first_started_at TEXT,
    ci_passing_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coding_sessions (
    session_id TEXT PRIMARY KEY,
    work_item_id INTEGER,
    repository TEXT NOT NULL,
    issue_number INTEGER,
    pr_number INTEGER,
    pr_url TEXT,
    model TEXT,
    model_snapshot TEXT,
    reasoning_effort TEXT,
    branch TEXT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    human_interventions INTEGER NOT NULL DEFAULT 0,
    human_episode_ids_json TEXT NOT NULL DEFAULT '[]',
    ci_attempts INTEGER NOT NULL DEFAULT 0,
    ci_successes INTEGER NOT NULL DEFAULT 0,
    ci_failures INTEGER NOT NULL DEFAULT 0,
    active_seconds REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    acceptance_tests_passed INTEGER NOT NULL DEFAULT 0,
    regression_tests_passed INTEGER NOT NULL DEFAULT 0,
    green INTEGER NOT NULL DEFAULT 0,
    termination_reason TEXT,
    FOREIGN KEY(work_item_id) REFERENCES work_item_metrics(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    delta_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES coding_sessions(session_id) ON DELETE CASCADE
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
CREATE INDEX IF NOT EXISTS idx_analysis_repository_eligible ON issue_analysis(repository, benchmark_eligible);
CREATE INDEX IF NOT EXISTS idx_work_item_repository_issue ON work_item_metrics(repository, issue_number);
CREATE INDEX IF NOT EXISTS idx_work_item_repository_pr ON work_item_metrics(repository, pr_number);
CREATE INDEX IF NOT EXISTS idx_coding_sessions_work_item ON coding_sessions(work_item_id, started_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_session_turn ON telemetry_events(session_id, turn_id);
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
            columns = {row[1] for row in connection.execute("PRAGMA table_info(issues)").fetchall()}
            if columns and "issue_type" not in columns:
                connection.execute(
                    "ALTER TABLE issues ADD COLUMN issue_type TEXT NOT NULL DEFAULT 'uncategorized'"
                )
            connection.executescript(SCHEMA)
            self._add_missing_columns(connection, "work_item_metrics", {
                "human_episode_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
                "acceptance_tests_passed": "INTEGER NOT NULL DEFAULT 0",
                "regression_tests_passed": "INTEGER NOT NULL DEFAULT 0",
                "green": "INTEGER NOT NULL DEFAULT 0",
                "autonomous_green": "INTEGER NOT NULL DEFAULT 0",
                "termination_reason": "TEXT",
                "completed_at": "TEXT",
            })
            self._add_missing_columns(connection, "coding_sessions", {
                "human_episode_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
                "acceptance_tests_passed": "INTEGER NOT NULL DEFAULT 0",
                "regression_tests_passed": "INTEGER NOT NULL DEFAULT 0",
                "green": "INTEGER NOT NULL DEFAULT 0",
                "termination_reason": "TEXT",
                "model_snapshot": "TEXT",
                "reasoning_effort": "TEXT",
            })
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT OR IGNORE INTO work_item_metrics(
                    work_key, repository, issue_number, issue_type, labels_json, updated_at
                )
                SELECT repository || ':issue:' || number, repository, number, issue_type, labels_json, ?
                FROM issues
                """,
                (now,),
            )
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _add_missing_columns(
        connection: sqlite3.Connection, table: str, definitions: dict[str, str]
    ) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def upsert_issue(self, issue: Issue, repository: str) -> None:
        issue_type = issue.issue_type if issue.issue_type != "uncategorized" else classify_issue(issue.labels)
        now = datetime.now(timezone.utc).isoformat()
        labels_json = json.dumps(sorted(set(issue.labels)))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO issues(
                    id, number, title, body, state, author, labels_json, issue_type,
                    html_url, created_at, repository
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(number) DO UPDATE SET
                    title=excluded.title, body=excluded.body, state=excluded.state,
                    author=excluded.author, labels_json=excluded.labels_json,
                    issue_type=excluded.issue_type, html_url=excluded.html_url,
                    repository=excluded.repository
                """,
                (
                    issue.id, issue.number, issue.title, issue.body, issue.state, issue.author,
                    labels_json, issue_type, issue.html_url, issue.created_at.isoformat(), repository,
                ),
            )
            work_key = self._work_key(repository, issue.number, None)
            connection.execute(
                """
                INSERT INTO work_item_metrics(
                    work_key, repository, issue_number, issue_type, labels_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_key) DO UPDATE SET
                    issue_type=excluded.issue_type,
                    labels_json=excluded.labels_json,
                    updated_at=excluded.updated_at
                """,
                (work_key, repository, issue.number, issue_type, labels_json, now),
            )
            connection.execute(
                """
                UPDATE work_item_metrics
                SET issue_type=?, labels_json=?, updated_at=?
                WHERE repository=? AND issue_number=?
                """,
                (issue_type, labels_json, now, repository, issue.number),
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

    def save_issue_analysis(self, analysis: IssueAnalysis) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO issue_analysis(
                    issue_number, repository, acceptance_criteria, github_labels_json,
                    actionable_labels_json, routing_labels_json, disposition_label,
                    benchmark_eligible, issue_text, embedding_json, embedding_model,
                    embedding_model_version, scope_score, solution_uncertainty_score,
                    public_interface_score, risk_compatibility_score, testing_burden_score,
                    complexity_score, complexity_class, classifier_model,
                    classifier_prompt_version, rubric_version, classifier_confidence,
                    classification_evidence_json, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_number) DO UPDATE SET
                    repository=excluded.repository,
                    acceptance_criteria=excluded.acceptance_criteria,
                    github_labels_json=excluded.github_labels_json,
                    actionable_labels_json=excluded.actionable_labels_json,
                    routing_labels_json=excluded.routing_labels_json,
                    disposition_label=excluded.disposition_label,
                    benchmark_eligible=excluded.benchmark_eligible,
                    issue_text=excluded.issue_text,
                    embedding_json=excluded.embedding_json,
                    embedding_model=excluded.embedding_model,
                    embedding_model_version=excluded.embedding_model_version,
                    scope_score=excluded.scope_score,
                    solution_uncertainty_score=excluded.solution_uncertainty_score,
                    public_interface_score=excluded.public_interface_score,
                    risk_compatibility_score=excluded.risk_compatibility_score,
                    testing_burden_score=excluded.testing_burden_score,
                    complexity_score=excluded.complexity_score,
                    complexity_class=excluded.complexity_class,
                    classifier_model=excluded.classifier_model,
                    classifier_prompt_version=excluded.classifier_prompt_version,
                    rubric_version=excluded.rubric_version,
                    classifier_confidence=excluded.classifier_confidence,
                    classification_evidence_json=excluded.classification_evidence_json,
                    analyzed_at=excluded.analyzed_at
                """,
                (
                    analysis.issue_number, analysis.repository, analysis.acceptance_criteria,
                    json.dumps(analysis.github_labels), json.dumps(analysis.actionable_labels),
                    json.dumps(analysis.routing_labels), analysis.disposition_label,
                    int(analysis.benchmark_eligible), analysis.issue_text,
                    json.dumps(analysis.embedding) if analysis.embedding is not None else None,
                    analysis.embedding_model, analysis.embedding_model_version,
                    analysis.scope_score, analysis.solution_uncertainty_score,
                    analysis.public_interface_score, analysis.risk_compatibility_score,
                    analysis.testing_burden_score, analysis.complexity_score,
                    analysis.complexity_class, analysis.classifier_model,
                    analysis.classifier_prompt_version, analysis.rubric_version,
                    analysis.classifier_confidence, json.dumps(analysis.classification_evidence),
                    analysis.analyzed_at.isoformat(),
                ),
            )

    def get_issue_analysis(
        self, issue_number: int, repository: str | None = None
    ) -> IssueAnalysis | None:
        with self.connect() as connection:
            row = (
                connection.execute(
                    "SELECT * FROM issue_analysis WHERE repository=? AND issue_number=?",
                    (repository, issue_number),
                ).fetchone()
                if repository
                else connection.execute(
                    "SELECT * FROM issue_analysis WHERE issue_number=?", (issue_number,)
                ).fetchone()
            )
        return self._issue_analysis_from_row(row) if row else None

    def historical_issue_runs(self, repository: str, exclude_issue_number: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, i.title, i.html_url, s.model, s.model_snapshot,
                       COALESCE(s.reasoning_effort, 'unknown') AS reasoning_effort,
                       s.total_cost_usd, s.green, s.human_interventions,
                       s.termination_reason, s.session_id
                FROM issue_analysis a
                JOIN issues i ON i.number=a.issue_number AND i.repository=a.repository
                JOIN work_item_metrics w ON w.issue_number=a.issue_number AND w.repository=a.repository
                JOIN coding_sessions s ON s.work_item_id=w.id
                WHERE a.repository=? AND a.issue_number<>? AND a.benchmark_eligible=1
                  AND s.model IS NOT NULL AND s.finished_at IS NOT NULL
                """,
                (repository, exclude_issue_number),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def prune_open_issues(self, repository: str, current_numbers: list[int]) -> int:
        with self.connect() as connection:
            if current_numbers:
                placeholders = ",".join("?" for _ in current_numbers)
                cursor = connection.execute(
                    f"DELETE FROM issues WHERE repository = ? AND state = 'open' AND number NOT IN ({placeholders})",
                    (repository, *current_numbers),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM issues WHERE repository = ? AND state = 'open'", (repository,)
                )
        return cursor.rowcount

    def start_coding_session(self, event: CodingSessionStart) -> TelemetryAck:
        with self.connect() as connection:
            work_item_id = self._ensure_work_item(
                connection, event.repository, event.issue_number, event.pr_number, event.pr_url
            )
            existing = connection.execute(
                "SELECT * FROM coding_sessions WHERE session_id = ?", (event.session_id,)
            ).fetchone()
            duplicate = existing is not None
            if not existing:
                connection.execute(
                    """
                    INSERT INTO coding_sessions(
                        session_id, work_item_id, repository, issue_number, pr_number, pr_url,
                        model, model_snapshot, reasoning_effort, branch, source, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.session_id, work_item_id, event.repository, event.issue_number,
                        event.pr_number, event.pr_url, event.model, event.model_snapshot,
                        event.reasoning_effort, event.branch, event.source,
                        event.started_at.isoformat(),
                    ),
                )
                if work_item_id:
                    connection.execute(
                        """
                        UPDATE work_item_metrics
                        SET session_count = session_count + 1,
                            first_started_at = COALESCE(first_started_at, ?), updated_at = ?
                        WHERE id = ?
                        """,
                        (event.started_at.isoformat(), event.started_at.isoformat(), work_item_id),
                    )
            else:
                previously_unbound = existing["work_item_id"] is None and work_item_id is not None
                changing_work_item = (
                    existing["work_item_id"] is not None
                    and work_item_id is not None
                    and existing["work_item_id"] != work_item_id
                )
                if changing_work_item and existing["turn_count"]:
                    raise ValueError("Attach the PR before recording turn telemetry")
                if changing_work_item:
                    connection.execute(
                        """
                        UPDATE work_item_metrics
                        SET session_count=MAX(session_count - 1, 0), updated_at=?
                        WHERE id=?
                        """,
                        (event.started_at.isoformat(), existing["work_item_id"]),
                    )
                connection.execute(
                    """
                    UPDATE coding_sessions SET
                        work_item_id=COALESCE(?, work_item_id),
                        issue_number=COALESCE(issue_number, ?), pr_number=COALESCE(pr_number, ?),
                        pr_url=COALESCE(pr_url, ?), model=COALESCE(model, ?),
                        model_snapshot=COALESCE(model_snapshot, ?),
                        reasoning_effort=COALESCE(reasoning_effort, ?), branch=COALESCE(branch, ?)
                    WHERE session_id = ?
                    """,
                    (
                        work_item_id, event.issue_number, event.pr_number, event.pr_url,
                        event.model, event.model_snapshot, event.reasoning_effort,
                        event.branch, event.session_id,
                    ),
                )
                if previously_unbound or changing_work_item:
                    connection.execute(
                        """
                        UPDATE work_item_metrics
                        SET session_count=session_count + 1,
                            first_started_at=COALESCE(first_started_at, ?), updated_at=?
                        WHERE id=?
                        """,
                        (event.started_at.isoformat(), event.started_at.isoformat(), work_item_id),
                    )
            aggregate = self._get_work_item_by_id(connection, work_item_id) if work_item_id else None
        return TelemetryAck(accepted=True, duplicate=duplicate, aggregate=aggregate)

    def record_turn(self, event: TurnTelemetry, pricing: ModelPricingCatalog) -> TelemetryAck:
        with self.connect() as connection:
            session = connection.execute(
                "SELECT * FROM coding_sessions WHERE session_id = ?", (event.session_id,)
            ).fetchone()
            if not session:
                raise ValueError("Coding session has not been started")
            existing = connection.execute(
                "SELECT 1 FROM telemetry_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if existing:
                aggregate = self._get_work_item_by_id(connection, session["work_item_id"])
                return TelemetryAck(accepted=True, duplicate=True, aggregate=aggregate)

            turn_seen = connection.execute(
                """
                SELECT 1 FROM telemetry_events
                WHERE session_id = ? AND turn_id = ? AND event_type = 'turn' LIMIT 1
                """,
                (event.session_id, event.turn_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO telemetry_events(
                    event_id, session_id, turn_id, event_type, source, occurred_at, delta_json
                ) VALUES (?, ?, ?, 'turn', ?, ?, ?)
                """,
                (
                    event.event_id, event.session_id, event.turn_id, event.source,
                    event.occurred_at.isoformat(), event.metrics.model_dump_json(),
                ),
            )
            self._apply_metric_delta(
                connection, session, event.metrics, pricing, event.occurred_at, count_turn=not turn_seen
            )
            aggregate = self._get_work_item_by_id(connection, session["work_item_id"])
        return TelemetryAck(accepted=True, duplicate=False, aggregate=aggregate)

    def finish_coding_session(self, event: CodingSessionFinish, pricing: ModelPricingCatalog) -> TelemetryAck:
        with self.connect() as connection:
            session = connection.execute(
                "SELECT * FROM coding_sessions WHERE session_id = ?", (event.session_id,)
            ).fetchone()
            if not session:
                raise ValueError("Coding session has not been started")
            existing = connection.execute(
                "SELECT 1 FROM telemetry_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if existing:
                aggregate = self._get_work_item_by_id(connection, session["work_item_id"])
                return TelemetryAck(accepted=True, duplicate=True, aggregate=aggregate)
            connection.execute(
                """
                INSERT INTO telemetry_events(
                    event_id, session_id, event_type, source, occurred_at, delta_json
                ) VALUES (?, ?, 'finish', ?, ?, ?)
                """,
                (
                    event.event_id, event.session_id, event.source, event.finished_at.isoformat(),
                    event.final_metrics.model_dump_json(),
                ),
            )
            self._apply_metric_delta(
                connection, session, event.final_metrics, pricing, event.finished_at, count_turn=False
            )
            connection.execute(
                """
                UPDATE coding_sessions
                SET finished_at=COALESCE(finished_at, ?),
                    outcome=CASE WHEN ?='unknown' THEN outcome ELSE ? END,
                    termination_reason=CASE WHEN ?='unknown' THEN termination_reason ELSE ? END
                WHERE session_id=?
                """,
                (
                    event.finished_at.isoformat(), event.outcome, event.outcome,
                    event.termination_reason, event.termination_reason, event.session_id,
                ),
            )
            aggregate_row = (
                connection.execute(
                    "SELECT green FROM work_item_metrics WHERE id=?", (session["work_item_id"],)
                ).fetchone()
                if session["work_item_id"]
                else None
            )
            completed = (
                bool(aggregate_row and aggregate_row["green"])
                or event.outcome in {"success", "failed", "abandoned"}
                or event.termination_reason != "unknown"
            )
            if completed and session["work_item_id"]:
                connection.execute(
                    """
                    UPDATE work_item_metrics
                    SET completed_at=COALESCE(completed_at, ?),
                        termination_reason=CASE WHEN ?='unknown' THEN termination_reason ELSE ? END,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        event.finished_at.isoformat(), event.termination_reason, event.termination_reason,
                        event.finished_at.isoformat(), session["work_item_id"],
                    ),
                )
            aggregate = self._get_work_item_by_id(connection, session["work_item_id"])
        return TelemetryAck(accepted=True, duplicate=False, aggregate=aggregate)

    def list_work_item_metrics(self, repository: str | None = None) -> list[WorkItemMetrics]:
        with self.connect() as connection:
            if repository:
                rows = connection.execute(
                    "SELECT * FROM work_item_metrics WHERE repository=? ORDER BY updated_at DESC",
                    (repository,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM work_item_metrics ORDER BY updated_at DESC"
                ).fetchall()
        return [self._work_item_from_row(row) for row in rows]

    def get_work_item_metrics(self, repository: str, issue_number: int) -> WorkItemMetrics | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM work_item_metrics
                WHERE repository=? AND issue_number=?
                ORDER BY (pr_number IS NOT NULL) DESC, updated_at DESC LIMIT 1
                """,
                (repository, issue_number),
            ).fetchone()
        return self._work_item_from_row(row) if row else None

    def cost_effectiveness(self, repository: str) -> list[CostEffectivenessGroup]:
        """Calculate CPGI per issue/model run, combining same-model sessions on one work item."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT w.id AS work_item_id, w.issue_type, s.model, s.outcome,
                       s.termination_reason, s.green, s.human_interventions,
                       s.human_episode_ids_json, s.total_cost_usd
                FROM coding_sessions s
                JOIN work_item_metrics w ON w.id=s.work_item_id
                WHERE w.repository=? AND s.model IS NOT NULL
                """,
                (repository,),
            ).fetchall()
        runs: dict[tuple[int, str], dict[str, object]] = {}
        for row in rows:
            key = (row["work_item_id"], row["model"].lower())
            current = runs.setdefault(key, {
                "model": row["model"],
                "issue_type": row["issue_type"],
                "spend": 0.0,
                "green": False,
                "completed": False,
                "episode_ids": set(),
                "legacy_interruptions": 0,
            })
            episode_ids = set(json.loads(row["human_episode_ids_json"]))
            current["spend"] = float(current["spend"]) + float(row["total_cost_usd"])
            current["green"] = bool(current["green"]) or bool(row["green"])
            current["completed"] = bool(current["completed"]) or (
                bool(row["green"])
                or row["outcome"] in {"success", "failed", "abandoned"}
                or (row["termination_reason"] not in {None, "unknown"})
            )
            current_episode_ids = current["episode_ids"]
            assert isinstance(current_episode_ids, set)
            current_episode_ids.update(episode_ids)
            current["legacy_interruptions"] = int(current["legacy_interruptions"]) + max(
                0, int(row["human_interventions"]) - len(episode_ids)
            )

        grouped: dict[str, dict[str, object]] = {}
        for run in runs.values():
            if not run["completed"]:
                continue
            model = str(run["model"])
            issue_type = str(run["issue_type"])
            key = model.lower()
            current = grouped.setdefault(key, {
                "model": model,
                "attempted": 0,
                "green": 0,
                "autonomous_green": 0,
                "spend": 0.0,
                "interruptions": 0,
                "issue_type_counts": {},
            })
            episode_ids = run["episode_ids"]
            assert isinstance(episode_ids, set)
            issue_type_counts = current["issue_type_counts"]
            assert isinstance(issue_type_counts, dict)
            interruptions = len(episode_ids) + int(run["legacy_interruptions"])
            green = int(bool(run["green"]))
            current["attempted"] = int(current["attempted"]) + 1
            current["green"] = int(current["green"]) + green
            current["autonomous_green"] = int(current["autonomous_green"]) + int(
                bool(green and interruptions == 0)
            )
            current["spend"] = float(current["spend"]) + float(run["spend"])
            current["interruptions"] = int(current["interruptions"]) + interruptions
            issue_type_counts[issue_type] = int(issue_type_counts.get(issue_type, 0)) + 1

        groups: list[CostEffectivenessGroup] = []
        for values in grouped.values():
            spend = round(float(values["spend"]), 8)
            green = int(values["green"])
            autonomous_green = int(values["autonomous_green"])
            interruptions = int(values["interruptions"])
            groups.append(CostEffectivenessGroup(
                model=str(values["model"]),
                issue_type_counts=dict(sorted(dict(values["issue_type_counts"]).items())),
                attempted_issues=int(values["attempted"]),
                green_issues=green,
                autonomous_green_issues=autonomous_green,
                total_spend_usd=spend,
                cost_per_green_issue_usd=round(spend / green, 8) if green else None,
                autonomous_cost_per_green_issue_usd=(
                    round(spend / autonomous_green, 8) if autonomous_green else None
                ),
                total_human_interruptions=interruptions,
                interruptions_per_green_issue=(
                    round(interruptions / green, 3) if green else None
                ),
            ))
        return sorted(groups, key=lambda group: group.model.lower())

    def add_session_event(self, event: SessionEvent) -> SessionRecord:
        received_at = datetime.now(timezone.utc)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO session_events(session_id, event_type, repository, issue_number, model,
                                           occurred_at, received_at, metrics_json, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id, event.event_type, event.repository, event.issue_number,
                    event.model, event.occurred_at.isoformat(), received_at.isoformat(),
                    json.dumps(event.metrics), event.outcome,
                ),
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

    def _apply_metric_delta(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        metrics: TurnMetricDelta,
        pricing: ModelPricingCatalog,
        occurred_at: datetime,
        count_turn: bool,
    ) -> None:
        input_tokens = sum(usage.input_tokens for usage in metrics.model_usage)
        cached_tokens = sum(usage.cached_input_tokens for usage in metrics.model_usage)
        cache_write_tokens = sum(usage.cache_write_tokens for usage in metrics.model_usage)
        output_tokens = sum(usage.output_tokens for usage in metrics.model_usage)
        reasoning_tokens = sum(usage.reasoning_tokens for usage in metrics.model_usage)
        cost = sum(pricing.cost(usage) for usage in metrics.model_usage)
        session_episode_ids = sorted(
            set(json.loads(session["human_episode_ids_json"]))
            | set(metrics.human_clarification_episode_ids)
        )
        session_new_episodes = (
            len(session_episode_ids) - len(json.loads(session["human_episode_ids_json"]))
        )
        session_human_interventions = (
            session["human_interventions"] + metrics.human_interventions + session_new_episodes
        )
        session_acceptance = (
            int(metrics.acceptance_tests_passed)
            if metrics.acceptance_tests_passed is not None
            else session["acceptance_tests_passed"]
        )
        session_regression = (
            int(metrics.regression_tests_passed)
            if metrics.regression_tests_passed is not None
            else session["regression_tests_passed"]
        )
        session_green = int(bool(session_acceptance and session_regression))
        inferred_model = metrics.model_usage[0].model if metrics.model_usage else None
        connection.execute(
            """
            UPDATE coding_sessions SET
                turn_count=turn_count + ?, human_interventions=?, human_episode_ids_json=?,
                ci_attempts=ci_attempts + ?, ci_successes=ci_successes + ?,
                ci_failures=ci_failures + ?, active_seconds=active_seconds + ?,
                input_tokens=input_tokens + ?, cached_input_tokens=cached_input_tokens + ?,
                cache_write_tokens=cache_write_tokens + ?, output_tokens=output_tokens + ?,
                reasoning_tokens=reasoning_tokens + ?, total_cost_usd=total_cost_usd + ?,
                acceptance_tests_passed=?, regression_tests_passed=?, green=?,
                model=COALESCE(model, ?)
            WHERE session_id=?
            """,
            (
                int(count_turn), session_human_interventions, json.dumps(session_episode_ids),
                metrics.ci_attempts,
                metrics.ci_successes, metrics.ci_failures, metrics.active_seconds,
                input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens,
                cost, session_acceptance, session_regression, session_green, inferred_model,
                session["session_id"],
            ),
        )
        work_item_id = session["work_item_id"]
        if not work_item_id:
            return
        row = connection.execute(
            "SELECT * FROM work_item_metrics WHERE id=?", (work_item_id,)
        ).fetchone()
        model_usage = json.loads(row["model_usage_json"])
        for usage in metrics.model_usage:
            current = model_usage.setdefault(usage.model, {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cost_usd": 0,
            })
            current["input_tokens"] += usage.input_tokens
            current["cached_input_tokens"] += usage.cached_input_tokens
            current["cache_write_tokens"] = current.get("cache_write_tokens", 0) + usage.cache_write_tokens
            current["output_tokens"] += usage.output_tokens
            current["reasoning_tokens"] += usage.reasoning_tokens
            current["cost_usd"] = round(float(current["cost_usd"]) + pricing.cost(usage), 8)

        files = sorted(set(json.loads(row["files_touched_json"])) | set(metrics.files_touched))
        modules = sorted(set(json.loads(row["modules_touched_json"])) | set(metrics.modules_touched))
        change_types = sorted(set(json.loads(row["change_types_json"])) | set(metrics.change_types))
        extra = {**json.loads(row["extra_json"]), **metrics.extra}
        existing_episode_ids = json.loads(row["human_episode_ids_json"])
        episode_ids = sorted(set(existing_episode_ids) | set(metrics.human_clarification_episode_ids))
        new_episode_count = len(episode_ids) - len(existing_episode_ids)
        human_interventions = (
            row["human_interventions"] + metrics.human_interventions + new_episode_count
        )
        acceptance_passed = (
            int(metrics.acceptance_tests_passed)
            if metrics.acceptance_tests_passed is not None
            else row["acceptance_tests_passed"]
        )
        regression_passed = (
            int(metrics.regression_tests_passed)
            if metrics.regression_tests_passed is not None
            else row["regression_tests_passed"]
        )
        green = int(bool(acceptance_passed and regression_passed))
        autonomous_green = int(bool(green and human_interventions == 0))
        ci_passing_at = row["ci_passing_at"] or (occurred_at.isoformat() if green else None)
        connection.execute(
            """
            UPDATE work_item_metrics SET
                turn_count=turn_count + ?, human_interventions=?, human_episode_ids_json=?,
                ci_attempts=ci_attempts + ?, ci_first_try_successes=ci_first_try_successes + ?,
                ci_successes=ci_successes + ?, ci_failures=ci_failures + ?,
                active_seconds=active_seconds + ?,
                logic_branches_added=logic_branches_added + ?,
                logic_branches_removed=logic_branches_removed + ?,
                pr_threads_multi_participant=pr_threads_multi_participant + ?,
                input_tokens=input_tokens + ?, cached_input_tokens=cached_input_tokens + ?,
                cache_write_tokens=cache_write_tokens + ?,
                output_tokens=output_tokens + ?, reasoning_tokens=reasoning_tokens + ?,
                total_cost_usd=total_cost_usd + ?, model_usage_json=?,
                acceptance_tests_passed=?, regression_tests_passed=?, green=?, autonomous_green=?,
                files_touched_json=?, modules_touched_json=?, change_types_json=?, extra_json=?,
                ci_passing_at=?, updated_at=?
            WHERE id=?
            """,
            (
                int(count_turn), human_interventions, json.dumps(episode_ids), metrics.ci_attempts,
                metrics.ci_first_try_successes, metrics.ci_successes, metrics.ci_failures,
                metrics.active_seconds, metrics.logic_branches_added, metrics.logic_branches_removed,
                metrics.pr_threads_multi_participant, input_tokens, cached_tokens, cache_write_tokens,
                output_tokens, reasoning_tokens, cost, json.dumps(model_usage),
                acceptance_passed, regression_passed, green, autonomous_green,
                json.dumps(files), json.dumps(modules),
                json.dumps(change_types), json.dumps(extra), ci_passing_at,
                occurred_at.isoformat(), work_item_id,
            ),
        )

    def _ensure_work_item(
        self,
        connection: sqlite3.Connection,
        repository: str,
        issue_number: int | None,
        pr_number: int | None,
        pr_url: str | None,
    ) -> int | None:
        if not issue_number and not pr_number:
            return None
        work_key = self._work_key(repository, issue_number, pr_number)
        if issue_number and pr_number:
            issue_key = self._work_key(repository, issue_number, None)
            issue_work_item = connection.execute(
                "SELECT id FROM work_item_metrics WHERE work_key=?", (issue_key,)
            ).fetchone()
            pr_work_item = connection.execute(
                "SELECT id FROM work_item_metrics WHERE work_key=?", (work_key,)
            ).fetchone()
            if issue_work_item and not pr_work_item:
                connection.execute(
                    """
                    UPDATE work_item_metrics
                    SET work_key=?, pr_number=?, pr_url=COALESCE(?, pr_url), updated_at=?
                    WHERE id=?
                    """,
                    (
                        work_key,
                        pr_number,
                        pr_url,
                        datetime.now(timezone.utc).isoformat(),
                        issue_work_item["id"],
                    ),
                )
        issue = None
        if issue_number:
            issue = connection.execute(
                "SELECT issue_type, labels_json FROM issues WHERE repository=? AND number=?",
                (repository, issue_number),
            ).fetchone()
        issue_type = issue["issue_type"] if issue else "uncategorized"
        labels_json = issue["labels_json"] if issue else "[]"
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO work_item_metrics(
                work_key, repository, issue_number, pr_number, pr_url,
                issue_type, labels_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_key) DO UPDATE SET
                pr_number=COALESCE(excluded.pr_number, pr_number),
                pr_url=COALESCE(excluded.pr_url, pr_url), updated_at=excluded.updated_at
            """,
            (work_key, repository, issue_number, pr_number, pr_url, issue_type, labels_json, now),
        )
        return connection.execute(
            "SELECT id FROM work_item_metrics WHERE work_key=?", (work_key,)
        ).fetchone()["id"]

    @staticmethod
    def _work_key(repository: str, issue_number: int | None, pr_number: int | None) -> str:
        if pr_number:
            return f"{repository}:pr:{pr_number}"
        return f"{repository}:issue:{issue_number}"

    def _get_work_item_by_id(
        self, connection: sqlite3.Connection, work_item_id: int | None
    ) -> WorkItemMetrics | None:
        if not work_item_id:
            return None
        row = connection.execute(
            "SELECT * FROM work_item_metrics WHERE id=?", (work_item_id,)
        ).fetchone()
        return self._work_item_from_row(row) if row else None

    @staticmethod
    def _issue_from_row(row: sqlite3.Row) -> Issue:
        recommendation = (
            Recommendation.model_validate_json(row["recommendation_json"])
            if row["recommendation_json"]
            else None
        )
        return Issue(
            id=row["id"], number=row["number"], title=row["title"], body=row["body"],
            state=row["state"], author=row["author"], labels=json.loads(row["labels_json"]),
            issue_type=row["issue_type"], html_url=row["html_url"], created_at=row["created_at"],
            recommendation=recommendation,
        )

    @staticmethod
    def _issue_analysis_from_row(row: sqlite3.Row) -> IssueAnalysis:
        return IssueAnalysis(
            issue_number=row["issue_number"], repository=row["repository"],
            acceptance_criteria=row["acceptance_criteria"],
            github_labels=json.loads(row["github_labels_json"]),
            actionable_labels=json.loads(row["actionable_labels_json"]),
            routing_labels=json.loads(row["routing_labels_json"]),
            disposition_label=row["disposition_label"],
            benchmark_eligible=bool(row["benchmark_eligible"]), issue_text=row["issue_text"],
            embedding=json.loads(row["embedding_json"]) if row["embedding_json"] else None,
            embedding_model=row["embedding_model"],
            embedding_model_version=row["embedding_model_version"],
            scope_score=row["scope_score"],
            solution_uncertainty_score=row["solution_uncertainty_score"],
            public_interface_score=row["public_interface_score"],
            risk_compatibility_score=row["risk_compatibility_score"],
            testing_burden_score=row["testing_burden_score"],
            complexity_score=row["complexity_score"], complexity_class=row["complexity_class"],
            classifier_model=row["classifier_model"],
            classifier_prompt_version=row["classifier_prompt_version"],
            rubric_version=row["rubric_version"],
            classifier_confidence=row["classifier_confidence"],
            classification_evidence=json.loads(row["classification_evidence_json"]),
            analyzed_at=row["analyzed_at"],
        )

    @staticmethod
    def _work_item_from_row(row: sqlite3.Row) -> WorkItemMetrics:
        return WorkItemMetrics(
            repository=row["repository"], issue_number=row["issue_number"], pr_number=row["pr_number"],
            pr_url=row["pr_url"], issue_type=row["issue_type"], labels=json.loads(row["labels_json"]),
            session_count=row["session_count"], turn_count=row["turn_count"],
            human_interventions=row["human_interventions"], ci_attempts=row["ci_attempts"],
            human_clarification_episode_ids=json.loads(row["human_episode_ids_json"]),
            ci_first_try_successes=row["ci_first_try_successes"], ci_successes=row["ci_successes"],
            ci_failures=row["ci_failures"], active_seconds=row["active_seconds"],
            logic_branches_added=row["logic_branches_added"],
            logic_branches_removed=row["logic_branches_removed"],
            pr_threads_multi_participant=row["pr_threads_multi_participant"],
            input_tokens=row["input_tokens"], cached_input_tokens=row["cached_input_tokens"],
            cache_write_tokens=row["cache_write_tokens"],
            output_tokens=row["output_tokens"], reasoning_tokens=row["reasoning_tokens"],
            total_cost_usd=row["total_cost_usd"],
            acceptance_tests_passed=bool(row["acceptance_tests_passed"]),
            regression_tests_passed=bool(row["regression_tests_passed"]),
            green=bool(row["green"]), autonomous_green=bool(row["autonomous_green"]),
            termination_reason=row["termination_reason"],
            model_usage=json.loads(row["model_usage_json"]),
            files_touched=json.loads(row["files_touched_json"]),
            modules_touched=json.loads(row["modules_touched_json"]),
            change_types=json.loads(row["change_types_json"]), extra=json.loads(row["extra_json"]),
            first_started_at=row["first_started_at"], ci_passing_at=row["ci_passing_at"],
            completed_at=row["completed_at"],
            updated_at=row["updated_at"],
        )
