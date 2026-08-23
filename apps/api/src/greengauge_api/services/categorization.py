ISSUE_TYPE_PRIORITY = (
    ("evals", {"eval", "evals", "evaluation", "benchmark"}),
    ("bug", {"bug", "defect", "incident", "regression"}),
    ("refactoring", {"refactor", "refactoring", "tech debt", "technical debt"}),
    ("feature", {"feature", "feature request", "enhancement"}),
    ("support", {"support", "question", "help wanted"}),
    ("ktlo", {"ktlo", "maintenance", "chore"}),
)


def classify_issue(labels: list[str]) -> str:
    normalized = {label.strip().lower() for label in labels}
    for issue_type, candidates in ISSUE_TYPE_PRIORITY:
        if normalized & candidates:
            return issue_type
    return "uncategorized"

