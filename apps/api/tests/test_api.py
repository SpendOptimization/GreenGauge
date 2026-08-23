import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def build_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("GREENGAUGE_DATABASE_PATH", str(tmp_path / "test.sqlite3"))
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
