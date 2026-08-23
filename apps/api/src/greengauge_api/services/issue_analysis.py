import json
import re
from datetime import datetime, timezone

import httpx

from ..models import ComplexityAssessment, Issue, IssueAnalysis
from .categorization import label_roles


RUBRIC_VERSION = "2026-08-23.v1"
PROMPT_VERSION = "2026-08-23.v1"


def extract_acceptance_criteria(body: str) -> str:
    for heading in ("acceptance criteria", "expected outcome", "expected behavior"):
        match = re.search(
            rf"(?ims)^#{{1,6}}\s*{heading}\s*$\s*(.*?)(?=^#{{1,6}}\s|\Z)", body
        )
        if match:
            return match.group(1).strip()
    return ""


def complexity_class(score: int) -> str:
    if score <= 3:
        return "Easy"
    if score <= 6:
        return "Medium"
    return "Hard"


class IssueAnalyzer:
    def __init__(
        self,
        api_key: str | None,
        classifier_model: str,
        embedding_model: str,
        timeout_seconds: float = 20,
    ):
        self.api_key = api_key
        self.classifier_model = classifier_model
        self.embedding_model = embedding_model
        self.timeout_seconds = timeout_seconds

    def analyze(self, issue: Issue, repository: str) -> IssueAnalysis:
        acceptance = extract_acceptance_criteria(issue.body)
        issue_text = "\n\n".join(
            part for part in (issue.title.strip(), issue.body.strip(), acceptance) if part
        )
        actionable, routing, disposition, eligible = label_roles(issue.labels)
        assessment, classifier_model = self._classify(issue_text)
        embedding, embedding_version = self._embed(issue_text)
        score = sum((
            assessment.scope_score,
            assessment.solution_uncertainty_score,
            assessment.public_interface_score,
            assessment.risk_compatibility_score,
            assessment.testing_burden_score,
        ))
        return IssueAnalysis(
            issue_number=issue.number,
            repository=repository,
            acceptance_criteria=acceptance,
            github_labels=sorted({label.strip().lower() for label in issue.labels}),
            actionable_labels=actionable,
            routing_labels=routing,
            disposition_label=disposition,
            benchmark_eligible=eligible,
            issue_text=issue_text,
            embedding=embedding,
            embedding_model=self.embedding_model if embedding else None,
            embedding_model_version=embedding_version,
            complexity_score=score,
            complexity_class=complexity_class(score),
            classifier_model=classifier_model,
            classifier_prompt_version=PROMPT_VERSION,
            rubric_version=RUBRIC_VERSION,
            analyzed_at=datetime.now(timezone.utc),
            **assessment.model_dump(),
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _classify(self, issue_text: str) -> tuple[ComplexityAssessment, str]:
        if not self.api_key:
            return self._heuristic(issue_text), "local-heuristic-v1"
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                name: {"type": "integer", "minimum": 0, "maximum": 2}
                for name in (
                    "scope_score", "solution_uncertainty_score", "public_interface_score",
                    "risk_compatibility_score", "testing_burden_score",
                )
            } | {
                "classifier_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "classification_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        name: {"type": "string"}
                        for name in (
                            "scope", "solution_uncertainty", "public_interface",
                            "risk_compatibility", "testing_burden",
                        )
                    },
                    "required": [
                        "scope", "solution_uncertainty", "public_interface",
                        "risk_compatibility", "testing_burden",
                    ],
                },
            },
            "required": [
                "scope_score", "solution_uncertainty_score", "public_interface_score",
                "risk_compatibility_score", "testing_burden_score", "classifier_confidence",
                "classification_evidence",
            ],
        }
        prompt = (
            "Score this software issue using the supplied rubric. Every category must be 0, 1, or 2. "
            "Scope: 0 one narrow behavior; 1 multiple edge cases; 2 multiple features/components. "
            "Solution uncertainty: 0 clear; 1 decisions required; 2 unclear design. "
            "Public interface: 0 internal; 1 visible behavior; 2 new CLI/config/API/dependency contract. "
            "Risk/compatibility: 0 low; 1 compatibility concerns; 2 destructive/security/archive/dependency risk. "
            "Testing burden: 0 one focused test; 1 multiple cases; 2 integration/E2E across paths. "
            "Evidence must be short and grounded only in the issue text.\n\nISSUE:\n" + issue_text
        )
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    "https://api.openai.com/v1/responses",
                    headers=self._headers(),
                    json={
                        "model": self.classifier_model,
                        "input": prompt,
                        "max_output_tokens": 700,
                        "store": False,
                        "text": {"format": {
                            "type": "json_schema", "name": "issue_complexity",
                            "strict": True, "schema": schema,
                        }},
                    },
                )
                response.raise_for_status()
                payload = response.json()
            output_text = "".join(
                content.get("text", "")
                for item in payload.get("output", [])
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            )
            return ComplexityAssessment.model_validate(json.loads(output_text)), payload.get(
                "model", self.classifier_model
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._heuristic(issue_text), "local-heuristic-v1"

    def _embed(self, issue_text: str) -> tuple[list[float] | None, str | None]:
        if not self.api_key or not issue_text:
            return None, None
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers=self._headers(),
                    json={"model": self.embedding_model, "input": issue_text},
                )
                response.raise_for_status()
                payload = response.json()
            return [float(value) for value in payload["data"][0]["embedding"]], payload.get(
                "model", self.embedding_model
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None, None

    @staticmethod
    def _heuristic(issue_text: str) -> ComplexityAssessment:
        text = issue_text.lower()
        scope = 2 if len(issue_text) > 1200 or any(x in text for x in ("multiple components", "across services")) else int(len(issue_text) > 450)
        uncertainty = 2 if any(x in text for x in ("design", "architecture", "unknown")) else int(any(x in text for x in ("decide", "investigate", "evaluate")))
        interface = 2 if any(x in text for x in ("new api", "cli", "configuration", "dependency")) else int(any(x in text for x in ("user-facing", "visible", "endpoint")))
        risk = 2 if any(x in text for x in ("security", "destructive", "archive", "race condition")) else int(any(x in text for x in ("compatib", "migration", "concurrent")))
        testing = 2 if any(x in text for x in ("integration", "end-to-end", "e2e", "multiple paths")) else int(any(x in text for x in ("edge case", "tests", "test cases")))
        return ComplexityAssessment(
            scope_score=scope,
            solution_uncertainty_score=uncertainty,
            public_interface_score=interface,
            risk_compatibility_score=risk,
            testing_burden_score=testing,
            classifier_confidence=0.35,
            classification_evidence={"fallback": "Local keyword heuristic; configure OPENAI_API_KEY for semantic scoring."},
        )
