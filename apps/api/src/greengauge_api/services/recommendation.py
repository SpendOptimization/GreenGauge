from datetime import datetime, timezone

from ..models import Issue, Recommendation, SimilarIssue


class RecommendationEngine:
    """Replace this implementation with similarity retrieval + a small LLM call."""

    def recommend(self, issue: Issue) -> Recommendation:
        text = f"{issue.title} {issue.body}".lower()
        frontier_signals = ("migration", "architecture", "security", "race condition", "distributed")
        balanced_signals = ("refactor", "api", "database", "authentication", "performance")

        if any(signal in text for signal in frontier_signals):
            model, model_class, confidence, cost, iterations = "Sol", "frontier", 0.88, 8.42, 1
            rationale = "Prior work with cross-cutting changes needed fewer retries on a frontier model, offsetting its higher token rate."
        elif any(signal in text for signal in balanced_signals):
            model, model_class, confidence, cost, iterations = "Sonnet", "balanced", 0.81, 3.18, 2
            rationale = "The issue spans a few connected components, but similar tasks were completed reliably without frontier-level reasoning."
        else:
            model, model_class, confidence, cost, iterations = "Terra", "terra", 0.86, 0.74, 2
            rationale = "The change appears localized and matches low-iteration UI and maintenance work where a value model had the lowest total cost."

        similar = [
            SimilarIssue(number=max(1, issue.number - 18), title="Related implementation", model_used=model,
                         total_cost_usd=round(cost * 0.92, 2), similarity=0.87,
                         url=f"https://github.com/rohanmalige/GreenGauge/issues/{max(1, issue.number - 18)}"),
            SimilarIssue(number=max(1, issue.number - 31), title="Comparable code path", model_used="Terra",
                         total_cost_usd=1.12, similarity=0.73,
                         url=f"https://github.com/rohanmalige/GreenGauge/issues/{max(1, issue.number - 31)}"),
        ]
        return Recommendation(
            model=model, model_class=model_class, confidence=confidence, expected_cost_usd=cost,
            expected_iterations=iterations, reasoning=rationale, generated_at=datetime.now(timezone.utc),
            similar_issues=similar,
        )

