from typing import Any

import httpx

from ..models import GitHubIssue


class GitHubClient:
    def __init__(self, token: str | None = None, api_url: str = "https://api.github.com"):
        self.api_url = api_url.rstrip("/")
        self.headers = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "user-agent": "GreenGauge/0.1",
        }
        if token:
            self.headers["authorization"] = f"Bearer {token}"

    async def list_open_issues(self, repository: str) -> list[GitHubIssue]:
        """Return open issues, excluding pull requests, from a repository."""
        owner, separator, repo = repository.partition("/")
        if not separator or not owner or not repo:
            raise ValueError("GITHUB_REPOSITORY must use owner/repo format")

        async with httpx.AsyncClient(timeout=15, headers=self.headers) as client:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/issues",
                params={"state": "open", "per_page": 100, "sort": "created", "direction": "desc"},
            )
            response.raise_for_status()
            payload: list[dict[str, Any]] = response.json()
        return [GitHubIssue.model_validate(item) for item in payload if "pull_request" not in item]

