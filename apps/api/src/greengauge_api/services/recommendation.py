import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..models import Issue, IssueAnalysis, Recommendation, SimilarIssue
from .issue_analysis import IssueAnalyzer

if TYPE_CHECKING:
    from ..db import Database


def jaccard(left: list[str], right: list[str]) -> float:
    left_set, right_set = set(left), set(right)
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 0.0


def embedding_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    cosine = sum(a * b for a, b in zip(left, right)) / denominator
    return max(0.0, min(1.0, (1 + cosine) / 2))


def issue_similarity(query: IssueAnalysis, candidate: dict[str, Any]) -> dict[str, float]:
    type_match = jaccard(query.actionable_labels, json.loads(candidate["actionable_labels_json"]))
    description = embedding_similarity(
        query.embedding,
        json.loads(candidate["embedding_json"]) if candidate["embedding_json"] else None,
    )
    complexity = 1 - abs(query.complexity_score - int(candidate["complexity_score"])) / 10
    total = 0.30 * type_match + 0.50 * description + 0.20 * complexity
    return {
        "type_match": type_match,
        "description_similarity": description,
        "complexity_similarity": complexity,
        "similarity": total,
    }


def model_class(model: str) -> str:
    lowered = model.lower()
    if "sol" in lowered or "opus" in lowered:
        return "frontier"
    if "sonnet" in lowered or "balanced" in lowered:
        return "balanced"
    return "terra"


class RecommendationEngine:
    def __init__(
        self,
        database: "Database",
        analyzer: IssueAnalyzer,
        repository: str,
        min_similarity: float = 0.60,
        min_success_rate: float = 0.60,
    ):
        self.database = database
        self.analyzer = analyzer
        self.repository = repository
        self.min_similarity = min_similarity
        self.min_success_rate = min_success_rate

    def recommend(self, issue: Issue, repository: str | None = None) -> Recommendation:
        repository = repository or self.repository
        analysis = self.analyzer.analyze(issue, repository)
        self.database.save_issue_analysis(analysis)
        if not analysis.benchmark_eligible:
            reason = (
                f"Excluded from model routing because label '{analysis.disposition_label}' requires triage."
                if analysis.disposition_label
                else "Excluded until the issue has an actionable accessibility, bug, documentation, or enhancement label."
            )
            return self._result(
                "Needs triage", "terra", analysis, reason, "excluded", confidence=1,
                status="excluded",
            )

        historical = self.database.historical_issue_runs(repository, issue.number)
        scored: list[dict[str, Any]] = []
        for run in historical:
            scores = issue_similarity(analysis, run)
            if scores["similarity"] >= self.min_similarity:
                scored.append(run | scores)

        aggregated: dict[tuple[int, str, str], dict[str, Any]] = {}
        for run in scored:
            key = (int(run["issue_number"]), str(run["model"]), str(run["reasoning_effort"]))
            current = aggregated.setdefault(key, run | {"run_cost": 0.0, "run_green": False})
            current["run_cost"] += float(run["total_cost_usd"])
            current["run_green"] = bool(current["run_green"] or run["green"])

        choices: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"weighted_cost": 0, "weighted_green": 0, "weight": 0}
        )
        for run in aggregated.values():
            key = (str(run["model"]), str(run["reasoning_effort"]))
            weight = float(run["similarity"])
            choices[key]["weighted_cost"] += weight * float(run["run_cost"])
            choices[key]["weighted_green"] += weight * int(bool(run["run_green"]))
            choices[key]["weight"] += weight

        eligible_choices: list[tuple[float, float, str, str]] = []
        for (model, effort), values in choices.items():
            success_rate = values["weighted_green"] / values["weight"] if values["weight"] else 0
            cpgi = (
                values["weighted_cost"] / values["weighted_green"]
                if values["weighted_green"]
                else math.inf
            )
            if success_rate >= self.min_success_rate and math.isfinite(cpgi):
                eligible_choices.append((cpgi, -success_rate, model, effort))

        if eligible_choices:
            cpgi, negative_success, chosen_model, effort = min(eligible_choices)
            success_rate = -negative_success
            comparable = sorted(
                (
                    run for run in aggregated.values()
                    if run["model"] == chosen_model and run["reasoning_effort"] == effort
                ),
                key=lambda run: run["similarity"],
                reverse=True,
            )
            similar = [self._similar_issue(run) for run in comparable[:3]]
            reason = (
                f"{chosen_model} at {effort} effort has the lowest similarity-weighted cost per "
                f"green issue (${cpgi:.4f}) among configurations meeting the "
                f"{self.min_success_rate:.0%} success threshold ({success_rate:.0%} observed)."
            )
            return self._result(
                chosen_model, model_class(chosen_model), analysis, reason, "similarity-weighted",
                confidence=success_rate, reasoning_effort=effort, cpgi=cpgi,
                success_rate=success_rate, similar=similar,
            )

        global_groups = [
            group for group in self.database.cost_effectiveness(repository)
            if group.cost_per_green_issue_usd is not None
            and group.green_issues > 0
            and group.green_issues / group.attempted_issues >= self.min_success_rate
        ]
        if global_groups:
            group = min(global_groups, key=lambda item: item.cost_per_green_issue_usd or math.inf)
            success_rate = group.green_issues / group.attempted_issues
            return self._result(
                group.model, model_class(group.model), analysis,
                f"No historical issue cleared the {self.min_similarity:.0%} similarity threshold. "
                f"Falling back to the lowest observed global cost per green issue for {group.model}; "
                "issue labels and complexity are retained for future matching.",
                "global-evidence", confidence=min(0.6, success_rate),
                cpgi=group.cost_per_green_issue_usd, success_rate=success_rate,
            )

        heuristic_model = (
            "gpt-5.6-terra" if analysis.complexity_score <= 3
            else "sonnet" if analysis.complexity_score <= 6
            else "gpt-5.6-sol"
        )
        return self._result(
            heuristic_model, model_class(heuristic_model), analysis,
            f"No completed comparable runs exist yet. Complexity is {analysis.complexity_class} "
            f"({analysis.complexity_score}/10), so this is a temporary complexity-only route.",
            "complexity-heuristic", confidence=analysis.classifier_confidence,
        )

    @staticmethod
    def _similar_issue(run: dict[str, Any]) -> SimilarIssue:
        return SimilarIssue(
            number=int(run["issue_number"]), title=str(run["title"]),
            model_used=str(run["model"]), total_cost_usd=round(float(run["run_cost"]), 8),
            similarity=round(float(run["similarity"]) * 100, 1), url=str(run["html_url"]),
            type_match=round(float(run["type_match"]), 3),
            description_similarity=round(float(run["description_similarity"]), 3),
            complexity_similarity=round(float(run["complexity_similarity"]), 3),
        )

    @staticmethod
    def _result(
        model: str,
        route_class: str,
        analysis: IssueAnalysis,
        reasoning: str,
        basis: str,
        confidence: float,
        status: str = "ready",
        reasoning_effort: str = "unknown",
        cpgi: float | None = None,
        success_rate: float | None = None,
        similar: list[SimilarIssue] | None = None,
    ) -> Recommendation:
        return Recommendation(
            model=model, model_class=route_class, confidence=max(0, min(1, confidence)),
            expected_cost_usd=0, expected_iterations=1, reasoning_effort=reasoning_effort,
            similarity_weighted_cpgi_usd=round(cpgi, 8) if cpgi is not None else None,
            weighted_success_rate=success_rate, complexity_score=analysis.complexity_score,
            complexity_class=analysis.complexity_class, recommendation_basis=basis,
            reasoning=reasoning, status=status, generated_at=datetime.now(timezone.utc),
            similar_issues=similar or [],
        )
