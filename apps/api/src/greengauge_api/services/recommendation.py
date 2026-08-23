from datetime import datetime, timezone

from ..models import Issue, Recommendation


class RecommendationEngine:
    """Replace this implementation with similarity retrieval + a small LLM call."""

    def recommend(self, issue: Issue) -> Recommendation:
        text = f"{issue.title} {issue.body}".lower()
        frontier_signals = ("migration", "architecture", "security", "race condition", "distributed")
        balanced_signals = ("refactor", "api", "database", "authentication", "performance")

        if any(signal in text for signal in frontier_signals):
            model, model_class, confidence, iterations = "Sol", "frontier", 0.88, 1
            rationale = "Preliminary complexity routing favors a frontier model. Historical CPGI evidence is shown separately when completed runs exist."
        elif any(signal in text for signal in balanced_signals):
            model, model_class, confidence, iterations = "Sonnet", "balanced", 0.81, 2
            rationale = "Preliminary complexity routing favors a balanced model. Historical CPGI evidence is shown separately when completed runs exist."
        else:
            model, model_class, confidence, iterations = "Terra", "terra", 0.86, 2
            rationale = "Preliminary complexity routing favors a value model. Historical CPGI evidence is shown separately when completed runs exist."

        return Recommendation(
            model=model, model_class=model_class, confidence=confidence, expected_cost_usd=0,
            expected_iterations=iterations, reasoning=rationale, generated_at=datetime.now(timezone.utc),
            similar_issues=[],
        )
