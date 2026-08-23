import importlib
from pathlib import Path

from fastapi.testclient import TestClient


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
            "labels": [{"name": "frontend"}],
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
        assert issues[0]["recommendation"]["model"] == "Terra"


def test_label_webhook_updates_issue_category_without_recommending_again(tmp_path, monkeypatch):
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
        assert response.json()["status"] == "metadata_updated"
        issue = client.get("/api/v1/issues/22").json()
        assert issue["issue_type"] == "bug"
        assert issue["recommendation"] is None


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
