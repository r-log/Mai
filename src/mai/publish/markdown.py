from mai.db.models import Report

SCHEMA_VERSION = 1


def report_to_markdown(report: Report, sources: list[str]) -> str:
    """Project a report to a versioned-front-matter ledger file (spec §9)."""
    lines = ["---", f"schema_version: {SCHEMA_VERSION}",
             f"id: {report.canonical_key}", f"core: {report.core}",
             f"status: {report.status}", "sources:"]
    lines += [f"  - {s}" for s in sources]
    lines += ["---", "", f"# {report.title}", ""]
    return "\n".join(lines)
