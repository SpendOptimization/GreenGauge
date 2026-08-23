#!/usr/bin/env python3
"""Privacy-preserving Codex lifecycle telemetry for GreenGauge.

The hook deliberately never transmits prompts, responses, commands, tool output, or
file contents. API failures are queued under .git and never block the Codex turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


TEST_COMMAND = re.compile(
    r"(^|\s|/)(pytest|vitest|jest|cargo\s+test|go\s+test|dotnet\s+test|"
    r"gradle\w*\s+test|make\s+test|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|"
    r"yarn\s+(run\s+)?test|gh\s+pr\s+checks)(\s|$)",
    re.IGNORECASE,
)
ACCEPTANCE_COMMAND = re.compile(r"\b(acceptance|e2e|end[- ]to[- ]end)\b", re.IGNORECASE)
REGRESSION_COMMAND = re.compile(r"\bregression\b", re.IGNORECASE)
LOGIC_BRANCH = re.compile(r"\b(if|else\s+if|elif|else|switch|case|match|catch|except)\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(cwd: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=4
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_root(cwd: Path) -> Path:
    value = run_git(cwd, "rev-parse", "--show-toplevel")
    return Path(value) if value else cwd


def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def infer_repository(root: Path) -> str:
    configured = os.getenv("GREENGAUGE_REPOSITORY") or os.getenv("GITHUB_REPOSITORY")
    if configured:
        return configured
    remote = run_git(root, "remote", "get-url", "spend") or run_git(root, "remote", "get-url", "origin")
    match = re.search(r"github\.com[/:]([^/]+/[^/.]+)(?:\.git)?$", remote)
    return match.group(1) if match else "unknown/unknown"


def infer_issue(branch: str) -> Optional[int]:
    configured = os.getenv("GREENGAUGE_ISSUE_NUMBER")
    if configured and configured.isdigit():
        return int(configured)
    match = re.search(r"(?:issue|issues|gh)[-_/]?(\d+)", branch, re.IGNORECASE)
    return int(match.group(1)) if match else None


def state_paths(root: Path, session_id: str) -> tuple[Path, Path]:
    git_dir = run_git(root, "rev-parse", "--git-dir")
    base = Path(git_dir) if git_dir else root / ".git"
    if not base.is_absolute():
        base = root / base
    state_dir = base / "greengauge-telemetry"
    state_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return state_dir / f"{digest}.json", state_dir / "outbox.jsonl"


def read_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def request_api(api_url: str, api_key: Optional[str], path: str, payload: dict[str, Any]) -> bool:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def post_or_queue(
    api_url: str,
    api_key: Optional[str],
    outbox: Path,
    path: str,
    payload: dict[str, Any],
) -> bool:
    if request_api(api_url, api_key, path, payload):
        return True
    with outbox.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"path": path, "payload": payload}) + "\n")
    return False


def flush_outbox(api_url: str, api_key: Optional[str], outbox: Path) -> None:
    if not outbox.exists():
        return
    try:
        queued = outbox.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    retained: list[str] = []
    for line in queued[:20]:
        try:
            item = json.loads(line)
            if not request_api(api_url, api_key, item["path"], item["payload"]):
                retained.append(line)
        except (KeyError, json.JSONDecodeError):
            continue
    retained.extend(queued[20:])
    if retained:
        outbox.write_text("\n".join(retained) + "\n", encoding="utf-8")
    else:
        outbox.unlink(missing_ok=True)


def find_exit_code(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        for key in ("exit_code", "exitCode", "status"):
            candidate = value.get(key)
            if isinstance(candidate, int):
                return candidate
        for nested in value.values():
            result = find_exit_code(nested)
            if result is not None:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = find_exit_code(nested)
            if result is not None:
                return result
    return None


def changed_files(root: Path) -> list[str]:
    tracked = run_git(root, "diff", "--name-only", "HEAD").splitlines()
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({path for path in tracked + untracked if path})


def infer_change_types(files: list[str]) -> list[str]:
    types: set[str] = set()
    for path in files:
        lower = path.lower()
        if any(part in lower for part in ("frontend", "apps/web", ".tsx", ".jsx", ".css")):
            types.add("frontend")
        if any(part in lower for part in ("backend", "apps/api", ".py", "/api/")):
            types.add("backend")
        if any(part in lower for part in ("migration", "schema", "database", "db.")):
            types.add("database")
        if any(part in lower for part in ("queue", "subscriber", "consumer", "async", "cdc")):
            types.add("cdc" if "cdc" in lower else "async")
        if any(part in lower for part in ("test", "spec", "__tests__")):
            types.add("tests")
        if lower.endswith((".md", ".mdx")) or "/docs/" in lower:
            types.add("docs")
        if any(part in lower for part in ("docker", ".github/", "terraform", "infra")):
            types.add("infra")
    return sorted(types or {"other"})


def branch_totals(root: Path) -> tuple[int, int]:
    diff = run_git(root, "diff", "--unified=0", "--no-color", "HEAD")
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += len(LOGIC_BRANCH.findall(line[1:]))
        elif line.startswith("-"):
            removed += len(LOGIC_BRANCH.findall(line[1:]))
    return added, removed


def transcript_model_calls(
    transcript_path: Optional[str], start_index: int, fallback_model: Optional[str]
) -> tuple[list[dict[str, Any]], int]:
    """Read token counters only; never return transcript messages or tool content."""
    if not transcript_path:
        return [], start_index
    path = Path(transcript_path)
    if not path.exists():
        return [], start_index
    calls: list[dict[str, Any]] = []
    token_event_index = 0
    current_model = fallback_model or "unknown"
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload") or {}
                if record.get("type") == "turn_context" and payload.get("model"):
                    current_model = str(payload["model"])
                if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                last_usage = (payload.get("info") or {}).get("last_token_usage")
                if not isinstance(last_usage, dict):
                    continue
                if token_event_index >= start_index:
                    calls.append({
                        "call_id": f"transcript-token-event:{token_event_index}",
                        "model": current_model,
                        "input_tokens": int(last_usage.get("input_tokens", 0)),
                        "cached_input_tokens": int(last_usage.get("cached_input_tokens", 0)),
                        "cache_write_tokens": int(last_usage.get("cache_write_input_tokens", 0)),
                        "output_tokens": int(last_usage.get("output_tokens", 0)),
                        "reasoning_tokens": int(last_usage.get("reasoning_output_tokens", 0)),
                    })
                token_event_index += 1
    except OSError:
        return [], start_index
    return calls, token_event_index


def context_output(event_name: str, message: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": message,
        }
    }))


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        incoming = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        incoming = {}
    session_id = str(incoming.get("session_id") or incoming.get("sessionId") or "unknown-session")
    turn_id = str(incoming.get("turn_id") or incoming.get("turnId") or "unknown-turn")
    cwd = Path(incoming.get("cwd") or os.getcwd())
    root = repo_root(cwd)
    load_dotenv(root)
    api_url = os.getenv("GREENGAUGE_API_URL", "http://localhost:8000")
    api_key = os.getenv("GREENGAUGE_MCP_API_KEY")
    repository = infer_repository(root)
    branch = run_git(root, "branch", "--show-current")
    issue_number = infer_issue(branch)
    transcript_path = incoming.get("transcript_path") or incoming.get("transcriptPath")
    state_path, outbox = state_paths(root, session_id)
    flush_outbox(api_url, api_key, outbox)
    state = read_state(state_path)

    if action == "session_start":
        _, token_event_count = transcript_model_calls(
            transcript_path, 0, incoming.get("model")
        )
        state = {
            "session_id": session_id,
            "repository": repository,
            "branch": branch,
            "issue_number": issue_number,
            "started_at": utc_now(),
            "transcript_path": transcript_path,
            "token_event_count": token_event_count,
            "prompt_count": 0,
            "ci_attempt_total": 0,
            "pending_human_interventions": 0,
            "pending_ci_attempts": 0,
            "pending_ci_successes": 0,
            "pending_ci_failures": 0,
            "pending_ci_first_try_successes": 0,
            "pending_all_ci_passed": False,
            "pending_acceptance_tests_passed": None,
            "pending_regression_tests_passed": None,
            "branch_added_total": 0,
            "branch_removed_total": 0,
        }
        post_or_queue(api_url, api_key, outbox, "/api/v1/telemetry/sessions/start", {
            "session_id": session_id,
            "repository": repository,
            "issue_number": issue_number,
            "model": incoming.get("model"),
            "branch": branch or None,
            "started_at": state["started_at"],
            "source": "codex-hook",
        })
        write_state(state_path, state)
        context_output(
            "SessionStart",
            f"GreenGauge telemetry sessionId={session_id}. Baseline lifecycle metrics are automatic. "
            "When the issue/PR is known, use attach_coding_session with this exact sessionId. "
            "The Stop hook reads exact token counters from the local transcript without sending content.",
        )
        return

    if not state:
        state = {
            "session_id": session_id, "repository": repository, "branch": branch,
            "issue_number": issue_number, "started_at": utc_now(), "prompt_count": 0,
            "ci_attempt_total": 0, "branch_added_total": 0, "branch_removed_total": 0,
        }

    if action == "prompt":
        state["prompt_count"] = int(state.get("prompt_count", 0)) + 1
        state["turn_started_monotonic"] = time.monotonic()
        state["active_turn_id"] = turn_id
        write_state(state_path, state)
        context_output(
            "UserPromptSubmit",
            f"GreenGauge current sessionId={session_id}, turnId={turn_id}. Before your final response, "
            "call record_turn_metrics once using these exact IDs for semantic metrics, but leave "
            "modelUsage empty because the Stop hook captures exact local transcript token deltas. "
            "If this prompt clarifies, corrects, adds missing context, or unblocks work, "
            "include a stable clarification episode ID (reuse it for follow-ups in the same episode). "
            "Report observed acceptance/regression results; never estimate or send content.",
        )
        return

    if action == "tool":
        tool_input = incoming.get("tool_input") or incoming.get("toolInput") or {}
        command = tool_input.get("command") or tool_input.get("cmd") or "" if isinstance(tool_input, dict) else ""
        if isinstance(command, str) and TEST_COMMAND.search(command):
            state["ci_attempt_total"] = int(state.get("ci_attempt_total", 0)) + 1
            state["pending_ci_attempts"] = int(state.get("pending_ci_attempts", 0)) + 1
            response = incoming.get("tool_response") or incoming.get("toolResponse") or {}
            exit_code = find_exit_code(response)
            if exit_code == 0:
                state["pending_ci_successes"] = int(state.get("pending_ci_successes", 0)) + 1
                state["pending_all_ci_passed"] = True
                if state["ci_attempt_total"] == 1:
                    state["pending_ci_first_try_successes"] = 1
            elif exit_code is not None:
                state["pending_ci_failures"] = int(state.get("pending_ci_failures", 0)) + 1
                state["pending_all_ci_passed"] = False
            if exit_code is not None and ACCEPTANCE_COMMAND.search(command):
                state["pending_acceptance_tests_passed"] = exit_code == 0
            if exit_code is not None and REGRESSION_COMMAND.search(command):
                state["pending_regression_tests_passed"] = exit_code == 0
        write_state(state_path, state)
        print("{}")
        return

    if action == "stop":
        files = changed_files(root)
        modules = sorted({path.split("/", 1)[0] for path in files})
        added_total, removed_total = branch_totals(root)
        added_delta = max(0, added_total - int(state.get("branch_added_total", 0)))
        removed_delta = max(0, removed_total - int(state.get("branch_removed_total", 0)))
        started = state.get("turn_started_monotonic")
        active_seconds = max(0, time.monotonic() - float(started)) if started is not None else 0
        model_calls, token_event_count = transcript_model_calls(
            transcript_path or state.get("transcript_path"),
            int(state.get("token_event_count", 0)),
            incoming.get("model"),
        )
        payload = {
            "event_id": f"hook-stop:{session_id}:{turn_id}",
            "session_id": session_id,
            "turn_id": turn_id,
            "occurred_at": utc_now(),
            "source": "codex-hook",
            "metrics": {
                "human_interventions": int(state.get("pending_human_interventions", 0)),
                "ci_attempts": int(state.get("pending_ci_attempts", 0)),
                "ci_first_try_successes": int(state.get("pending_ci_first_try_successes", 0)),
                "ci_successes": int(state.get("pending_ci_successes", 0)),
                "ci_failures": int(state.get("pending_ci_failures", 0)),
                "all_ci_passed": bool(state.get("pending_all_ci_passed", False)),
                "acceptance_tests_passed": state.get("pending_acceptance_tests_passed"),
                "regression_tests_passed": state.get("pending_regression_tests_passed"),
                "active_seconds": round(active_seconds, 3),
                "logic_branches_added": added_delta,
                "logic_branches_removed": removed_delta,
                "files_touched": files,
                "modules_touched": modules,
                "change_types": infer_change_types(files),
                "model_usage": model_calls,
                "extra": {
                    "collector_version": 2,
                    "token_source": "codex-transcript" if model_calls else "unavailable",
                },
            },
        }
        post_or_queue(
            api_url, api_key, outbox,
            f"/api/v1/telemetry/sessions/{urllib.parse.quote(session_id, safe='')}/turns",
            payload,
        )
        state.update({
            "pending_human_interventions": 0,
            "pending_ci_attempts": 0,
            "pending_ci_successes": 0,
            "pending_ci_failures": 0,
            "pending_ci_first_try_successes": 0,
            "pending_all_ci_passed": False,
            "pending_acceptance_tests_passed": None,
            "pending_regression_tests_passed": None,
            "branch_added_total": added_total,
            "branch_removed_total": removed_total,
            "token_event_count": token_event_count,
            "transcript_path": transcript_path or state.get("transcript_path"),
        })
        state.pop("turn_started_monotonic", None)
        write_state(state_path, state)
        print("{}")
        return

    if action == "session_end":
        post_or_queue(
            api_url, api_key, outbox,
            f"/api/v1/telemetry/sessions/{urllib.parse.quote(session_id, safe='')}/finish",
            {
                "event_id": f"hook-end:{session_id}",
                "session_id": session_id,
                "finished_at": utc_now(),
                "outcome": "unknown",
                "source": "codex-hook",
                "final_metrics": {},
            },
        )
        print("{}")
        return

    print("{}")


if __name__ == "__main__":
    main()
