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
                        model, branch, source, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.session_id, work_item_id, event.repository, event.issue_number,
                        event.pr_number, event.pr_url, event.model, event.branch, event.source,
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
                        pr_url=COALESCE(pr_url, ?), model=COALESCE(model, ?), branch=COALESCE(branch, ?)
                    WHERE session_id = ?
                    """,
                    (
                        work_item_id, event.issue_number, event.pr_number, event.pr_url,
                        event.model, event.branch, event.session_id,
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

        grouped: dict[tuple[str, str], dict[str, int | float]] = {}
        for run in runs.values():
            if not run["completed"]:
                continue
            model = str(run["model"])
            issue_type = str(run["issue_type"])
            key = (model, issue_type)
            current = grouped.setdefault(key, {
                "attempted": 0,
                "green": 0,
                "autonomous_green": 0,
                "spend": 0.0,
                "interruptions": 0,
            })
            episode_ids = run["episode_ids"]
            assert isinstance(episode_ids, set)
            interruptions = len(episode_ids) + int(run["legacy_interruptions"])
            green = int(bool(run["green"]))
            current["attempted"] += 1
            current["green"] += green
            current["autonomous_green"] += int(bool(green and interruptions == 0))
            current["spend"] += float(run["spend"])
            current["interruptions"] += interruptions

        groups: list[CostEffectivenessGroup] = []
        for (model, issue_type), values in grouped.items():
            spend = round(float(values["spend"]), 8)
            green = int(values["green"])
            autonomous_green = int(values["autonomous_green"])
            interruptions = int(values["interruptions"])
            groups.append(CostEffectivenessGroup(
                model=model,
                issue_type=issue_type,
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
        return sorted(groups, key=lambda group: (group.issue_type, group.model.lower()))

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
