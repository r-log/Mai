from mai.db.models import Report
from mai.publish.markdown import report_to_markdown

SCHEMA_VERSION = 1


def test_report_to_markdown_emits_versioned_frontmatter():
    report = Report(
        id="11111111-1111-1111-1111-111111111111",
        canonical_key="ips:r1842", core="zero",
        title="Agro from pet doesnt work", status="open",
    )
    md = report_to_markdown(report, sources=["ips:r1842"])
    assert md.startswith("---\n")
    assert f"schema_version: {SCHEMA_VERSION}" in md
    assert 'id: ips:r1842' in md
    assert "core: zero" in md
    assert "status: open" in md
    assert "sources:\n  - ips:r1842" in md
    assert md.rstrip().endswith("# Agro from pet doesnt work")
