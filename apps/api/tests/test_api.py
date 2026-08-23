import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from greengauge_api.models import Issue, IssueAnalysis
from greengauge_api.services.issue_analysis import IssueAnalyzer, extract_acceptance_criteria
from greengauge_api.services.recommendation import RecommendationEngine, embedding_similarity, jaccard


def build_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("GREENGAUGE_DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("GITHUB_REPOSITORY", "rohanmalige/GreenGauge")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    import greengauge_api.config as config
    config.get_settings.cache_clear()
    import greengauge_api.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_seeded_issue_queue(tmp_path, monkeypatch):
    with build_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/issues")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["issues"][0]["recommendation"]["model"]


def test_session_event_round_trip(tmp_path, monkeypatch):
    with build_client(tmp_path, monkeypatch) as client:
        event = {
            "session_id": "session-123",
            "event_type": "finished",
            "repository": "rohanmalige/GreenGauge",
            "issue_number": 84,
            "model": "Terra",
            "occurred_at": "2026-08-23T12:00:00Z",
            "metrics": {"tool_calls": 8, "files_touched": 4},
            "outcome": "success",
        }
        assert client.post("/api/v1/sessions/events", json=event).status_code == 200
        events = client.get("/api/v1/sessions").json()
        assert events[0]["session_id"] == "session-123"
        assert events[0]["metrics"]["tool_calls"] == 8


def test_similarity_weighted_recommendation_uses_labels_description_and_complexity():
    assert extract_acceptance_criteria(
        "## Expected behavior\nFallback\n## Acceptance criteria\nPreferred\n"
    ) == "Preferred"
    query = IssueAnalysis(
        issue_number=20, repository="acme/repo", github_labels=["bug"],
        actionable_labels=["bug"], benchmark_eligible=True, issue_text="Fix webhook retry race",
        embedding=[1, 0], embedding_model="test", embedding_model_version="test",
        scope_score=1, solution_uncertainty_score=1, public_interface_score=0,
        risk_compatibility_score=1, testing_burden_score=1, complexity_score=4,
        complexity_class="Medium", classifier_model="test", classifier_prompt_version="v1",
        rubric_version="v1", classifier_confidence=0.9, analyzed_at=datetime.now(timezone.utc),
    )

    class FakeAnalyzer:
        def analyze(self, _issue, _repository):
            return query

    class FakeDatabase:
        def save_issue_analysis(self, _analysis):
            pass

        def historical_issue_runs(self, _repository, _exclude):
            base = {
                "issue_number": 10, "title": "Webhook race", "html_url": "https://example/10",
                "actionable_labels_json": json.dumps(["bug"]),
                "embedding_json": json.dumps([1, 0]), "complexity_score": 5,
                "reasoning_effort": "low", "green": 1, "human_interventions": 0,
                "termination_reason": "green", "session_id": "one",
            }
            return [
                base | {"model": "gpt-5.6-terra", "total_cost_usd": 1.0},
                base | {"model": "gpt-5.6-sol", "total_cost_usd": 2.0},
            ]

        def cost_effectiveness(self, _repository):
            return []

    issue = Issue(
        id=20, number=20, title="Fix webhook retry race", author="dev", labels=["bug"],
        html_url="https://example/20", created_at=datetime.now(timezone.utc),
    )
    recommendation = RecommendationEngine(
        FakeDatabase(), FakeAnalyzer(), "acme/repo"
    ).recommend(issue)
    assert jaccard(["bug", "accessibility"], ["bug"]) == 0.5
    assert embedding_similarity([1, 0], [1, 0]) == 1
    assert recommendation.model == "gpt-5.6-terra"
    assert recommendation.recommendation_basis == "similarity-weighted"
    assert recommendation.similarity_weighted_cpgi_usd == 1
    assert recommendation.similar_issues[0].similarity == 98


def test_openai_issue_analysis_parses_structured_output_and_embedding(monkeypatch):
    assessment = {
        "scope_score": 1,
        "solution_uncertainty_score": 1,
        "public_interface_score": 2,
        "risk_compatibility_score": 0,
        "testing_burden_score": 1,
        "classifier_confidence": 0.91,
        "classification_evidence": {
            "scope": "Several cases", "solution_uncertainty": "One decision",
            "public_interface": "New API", "risk_compatibility": "Low risk",
            "testing_burden": "Several tests",
        },
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeClient:
        response_request = None

        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, **kwargs):
            if url.endswith("/responses"):
                FakeClient.response_request = kwargs["json"]
                return FakeResponse({
                    "model": "gpt-5-nano-2025-08-07",
                    "output": [{"content": [{"type": "output_text", "text": json.dumps(assessment)}]}],
                })
            return FakeResponse({"model": "text-embedding-3-small", "data": [{"embedding": [0.1, 0.2]}]})

    monkeypatch.setattr("greengauge_api.services.issue_analysis.httpx.Client", FakeClient)
    issue = Issue(
        id=30, number=30, title="Add API", body="## Acceptance criteria\nReturns 200.",
        author="dev", labels=["enhancement", "good first issue"],
        html_url="https://example/30", created_at=datetime.now(timezone.utc),
    )
    analysis = IssueAnalyzer(
        "test-key", "gpt-5-nano-2025-08-07", "text-embedding-3-small"
    ).analyze(issue, "acme/repo")
    assert analysis.complexity_score == 5
    assert analysis.complexity_class == "Medium"
    assert analysis.embedding == [0.1, 0.2]
    assert analysis.classifier_model == "gpt-5-nano-2025-08-07"
    assert FakeClient.response_request["reasoning"] == {"effort": "minimal"}
    assert FakeClient.response_request["max_output_tokens"] == 1000
    assert analysis.actionable_labels == ["enhancement"]
    assert analysis.routing_labels == ["good first issue"]


def test_github_sync_replaces_demo_queue(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    import greengauge_api.main as main

    async def fake_list_open_issues(_repository):
        from greengauge_api.models import GitHubIssue
        return [GitHubIssue.model_validate({
            "id": 998,
            "number": 12,
            "title": "Add repository picker",
            "body": "Let the user switch repositories.",
            "state": "open",
            "user": {"login": "rohan"},
            "labels": [{"name": "enhancement"}, {"name": "good first issue"}],
            "html_url": "https://github.com/rohanmalige/GreenGauge/issues/12",
            "created_at": "2026-08-23T12:00:00Z",
        })]

    monkeypatch.setattr(main.github_client, "list_open_issues", fake_list_open_issues)
    with client:
        response = client.post("/api/v1/github/sync")
        assert response.status_code == 200
        assert response.json()["imported"] == 1
        issues = client.get("/api/v1/issues").json()["issues"]
        assert [issue["number"] for issue in issues] == [12]
        assert issues[0]["recommendation"]["status"] == "ready"
        analysis = client.get("/api/v1/issues/12/analysis").json()
        assert analysis["actionable_labels"] == ["enhancement"]
        assert analysis["routing_labels"] == ["good first issue"]
        assert analysis["benchmark_eligible"] is True


def test_label_webhook_updates_issue_category_and_recommendation(tmp_path, monkeypatch):
    with build_client(tmp_path, monkeypatch) as client:
        payload = {
            "action": "labeled",
            "issue": {
                "id": 500,
                "number": 22,
                "title": "Repair webhook retries",
                "body": "Retries can race.",
                "state": "open",
                "user": {"login": "dev"},
                "labels": [{"name": "bug"}, {"name": "backend"}],
                "html_url": "https://github.com/SpendOptimization/GreenGauge/issues/22",
                "created_at": "2026-08-23T12:00:00Z",
            },
            "repository": {"full_name": "SpendOptimization/GreenGauge"},
        }
        response = client.post(
            "/api/v1/github/webhooks",
            headers={"X-GitHub-Event": "issues"},
            json=payload,
        )
        assert response.status_code == 202
        assert response.json()["status"] == "recommendation_updated"
        issue = client.get("/api/v1/issues/22").json()
        assert issue["issue_type"] == "bug"
        assert issue["recommendation"]["complexity_score"] is not None
        analysis = client.get("/api/v1/issues/22/analysis").json()
        assert analysis["actionable_labels"] == ["bug"]
        assert analysis["benchmark_eligible"] is True


def test_turn_telemetry_aggregates_sessions_and_is_idempotent(tmp_path, monkeypatch):
    with build_client(tmp_path, monkeypatch) as client:
        start = {
            "session_id": "codex-session-a",
            "repository": "rohanmalige/GreenGauge",
            "issue_number": 84,
            "model": "terra",
            "branch": "issue-84",
            "started_at": "2026-08-23T12:00:00Z",
            "source": "codex-hook",
        }
        assert client.post("/api/v1/telemetry/sessions/start", json=start).status_code == 200
        duplicate = client.post("/api/v1/telemetry/sessions/start", json=start).json()
        assert duplicate["duplicate"] is True

        hook_turn = {
            "event_id": "hook-stop:codex-session-a:turn-1",
            "session_id": "codex-session-a",
            "turn_id": "turn-1",
            "occurred_at": "2026-08-23T12:01:00Z",
            "source": "codex-hook",
            "metrics": {
                "ci_attempts": 1,
                "ci_failures": 1,
                "active_seconds": 60,
                "files_touched": ["apps/api/src/main.py"],
                "modules_touched": ["apps"],
                "change_types": ["backend"],
            },
        }
        assert client.post(
            "/api/v1/telemetry/sessions/codex-session-a/turns", json=hook_turn
        ).status_code == 200

        mcp_turn = {
            "event_id": "mcp-turn:codex-session-a:turn-1",
            "session_id": "codex-session-a",
            "turn_id": "turn-1",
            "occurred_at": "2026-08-23T12:01:01Z",
            "source": "mcp",
            "metrics": {
                "model_usage": [{
                    "call_id": "call-1",
                    "model": "terra",
                    "input_tokens": 1000,
                    "cached_input_tokens": 100,
                    "cache_write_tokens": 100,
                    "output_tokens": 500,
                    "reasoning_tokens": 200,
                }],
                "human_clarification_episode_ids": ["clarification-turn-1"],
                "acceptance_tests_passed": True,
                "regression_tests_passed": True,
                "change_types": ["database"],
                "extra": {"task_phase": "implementation"},
            },
        }
        assert client.post(
            "/api/v1/telemetry/sessions/codex-session-a/turns", json=mcp_turn
        ).status_code == 200
        retry = client.post(
            "/api/v1/telemetry/sessions/codex-session-a/turns", json=mcp_turn
        ).json()
        assert retry["duplicate"] is True

        # The PR does not exist when Codex starts from an issue. Attaching it later
        # promotes the issue aggregate without dropping the turns already recorded.
        attached = {
            **start,
            "pr_number": 99,
            "pr_url": "https://github.com/rohanmalige/GreenGauge/pull/99",
        }
        assert client.post("/api/v1/telemetry/sessions/start", json=attached).status_code == 200

        second = {
            **attached,
            "session_id": "codex-session-b",
            "started_at": "2026-08-23T12:05:00Z",
        }
        assert client.post("/api/v1/telemetry/sessions/start", json=second).status_code == 200

        metrics = client.get("/api/v1/metrics/work-items/84").json()
        assert metrics["pr_number"] == 99
        assert metrics["session_count"] == 2
        assert metrics["turn_count"] == 1
        assert metrics["ci_attempts"] == 1
        assert metrics["ci_failures"] == 1
        assert metrics["input_tokens"] == 1000
        assert metrics["cached_input_tokens"] == 100
        assert metrics["cache_write_tokens"] == 100
        assert metrics["output_tokens"] == 500
        assert metrics["reasoning_tokens"] == 200
        assert metrics["total_cost_usd"] == 0.0009775
        assert metrics["green"] is True
        assert metrics["autonomous_green"] is False
        assert metrics["human_interventions"] == 1
        assert metrics["change_types"] == ["backend", "database"]
        assert metrics["extra"]["task_phase"] == "implementation"

        finish = {
            "event_id": "finish-a",
            "session_id": "codex-session-a",
            "finished_at": "2026-08-23T12:02:00Z",
            "outcome": "success",
            "termination_reason": "green",
            "final_metrics": {},
        }
        assert client.post(
            "/api/v1/telemetry/sessions/codex-session-a/finish", json=finish
        ).status_code == 200

        failed_start = {
            **start,
            "session_id": "codex-session-failed",
            # Seed issue 83 is a bug while successful issue 84 is uncategorized.
            # Both must contribute to the same model comparison.
            "issue_number": 83,
            "started_at": "2026-08-23T13:00:00Z",
        }
        assert client.post("/api/v1/telemetry/sessions/start", json=failed_start).status_code == 200
        failed_turn = {
            "event_id": "failed-turn",
            "session_id": "codex-session-failed",
            "turn_id": "turn-1",
            "occurred_at": "2026-08-23T13:01:00Z",
            "metrics": {"model_usage": [{"model": "terra", "input_tokens": 1000}]},
        }
        assert client.post(
            "/api/v1/telemetry/sessions/codex-session-failed/turns", json=failed_turn
        ).status_code == 200
        failed_finish = {
            "event_id": "finish-failed",
            "session_id": "codex-session-failed",
            "finished_at": "2026-08-23T13:02:00Z",
            "outcome": "failed",
            "termination_reason": "gave_up",
            "final_metrics": {},
        }
        assert client.post(
            "/api/v1/telemetry/sessions/codex-session-failed/finish", json=failed_finish
        ).status_code == 200

        report = client.get("/api/v1/metrics/cost-effectiveness").json()
        group = next(item for item in report["groups"] if item["model"] == "terra")
        assert len(report["groups"]) == 1
        assert group["issue_type_counts"] == {"bug": 1, "uncategorized": 1}
        assert group["attempted_issues"] == 2
        assert group["green_issues"] == 1
        assert group["autonomous_green_issues"] == 0
        assert group["total_spend_usd"] == 0.0012275
        assert group["cost_per_green_issue_usd"] == 0.0012275
        assert group["autonomous_cost_per_green_issue_usd"] is None
        assert group["interruptions_per_green_issue"] == 1
