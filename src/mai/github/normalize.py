from mai.contracts import IntakeEvent
from mai.github.constants import SOURCE_GH_ISSUE, SOURCE_GH_PR


def normalize_issue(repo_full_name: str, core: str, item: dict) -> IntakeEvent | None:
    """Map a GitHub issue to an IntakeEvent. Returns None if the item is a PR."""
    if "pull_request" in item:
        return None
    return IntakeEvent(
        source_type=SOURCE_GH_ISSUE,
        source_id=f"{repo_full_name}#{item['number']}",
        title=item["title"],
        core=core,
        status=item["state"],
        repo_full_name=repo_full_name,
        raw_payload=item,
    )


def normalize_pull(repo_full_name: str, core: str, item: dict) -> IntakeEvent:
    """Map a GitHub pull request to an IntakeEvent."""
    status = "merged" if item.get("merged_at") else item["state"]
    return IntakeEvent(
        source_type=SOURCE_GH_PR,
        source_id=f"{repo_full_name}#{item['number']}",
        title=item["title"],
        core=core,
        status=status,
        repo_full_name=repo_full_name,
        raw_payload=item,
    )
