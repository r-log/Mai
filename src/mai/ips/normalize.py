from mai.contracts import IntakeEvent
from mai.ips.parse import parse_bug_page, parse_bug_url

SOURCE_IPS = "ips"


def normalize_ips(url: str, markdown: str) -> IntakeEvent:
    core, bug_id = parse_bug_url(url)
    bug = parse_bug_page(markdown)
    status = (bug.status or "open").strip().lower()
    return IntakeEvent(
        source_type=SOURCE_IPS,
        source_id=bug_id,
        title=bug.title,
        core=core,
        status=status,
        repo_full_name=None,
        raw_payload={
            "url": url,
            "markdown": markdown,
            "main_category": bug.main_category,
            "sub_category": bug.sub_category,
            "version": bug.version,
            "milestone": bug.milestone,
            "priority": bug.priority,
            "implemented_version": bug.implemented_version,
        },
    )
