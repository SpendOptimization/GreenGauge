ACTIONABLE_LABELS = {"accessibility", "bug", "documentation", "enhancement"}
ROUTING_LABELS = {"good first issue", "help wanted"}
DISPOSITION_LABELS = {"question", "duplicate", "invalid", "wontfix"}
ISSUE_TYPE_PRIORITY = ("bug", "enhancement", "documentation", "accessibility")


def classify_issue(labels: list[str]) -> str:
    normalized = {label.strip().lower() for label in labels}
    for issue_type in ISSUE_TYPE_PRIORITY:
        if issue_type in normalized:
            return issue_type
    return "uncategorized"


def label_roles(labels: list[str]) -> tuple[list[str], list[str], str | None, bool]:
    normalized = {label.strip().lower() for label in labels}
    actionable = sorted(normalized & ACTIONABLE_LABELS)
    routing = sorted(normalized & ROUTING_LABELS)
    dispositions = sorted(normalized & DISPOSITION_LABELS)
    disposition = dispositions[0] if dispositions else None
    return actionable, routing, disposition, bool(actionable and not disposition)
