from mai.db.models import DriftObservation, Report, Verification
from mai.publish.render import render_drift_page, render_home, render_report_page
from mai.publish.views import ReportBundle


def _bundle(**kw):
    base = dict(
        report=Report(canonical_key="ips:r1", core="zero", title="Raw title", status="completed"),
        enrichment={"normalized_title": "Pet threat", "english_summary": "Pet loses threat.",
                    "steps_to_reproduce": ["attack", "send pet"],
                    "affected_entities": {"npc": ["Devilsaur"], "zone": []}},
        verification=Verification(report_id="x", verdict="fixed_confirmed", confidence=0.95,
                                  evidence=[]),
        correlations=[("gh_pr:zero/server#7", "explicit_ref", 1.0)])
    base.update(kw)
    return ReportBundle(**base)


def test_render_report_page_full():
    md = render_report_page(_bundle())
    assert md.startswith("---\n")
    assert "schema_version: 2" in md
    assert "id: ips:r1" in md
    assert 'title: "Pet threat"' in md          # enriched title wins over raw
    assert "verdict: fixed_confirmed" in md
    assert "confidence: 0.95" in md
    assert "## Summary" in md and "Pet loses threat." in md
    assert "## Steps to reproduce" in md and "- attack" in md
    assert "**npc:** Devilsaur" in md            # empty zone list omitted
    assert "## Evidence" in md
    assert "`gh_pr:zero/server#7` (explicit_ref, score 1.00)" in md


def test_render_report_page_minimal_falls_back_to_raw_title():
    md = render_report_page(_bundle(enrichment=None, verification=None, correlations=[]))
    assert 'title: "Raw title"' in md
    assert "verdict: open" in md
    assert "## Summary" not in md               # nothing to summarize


def test_render_drift_page_sorts_by_diverged():
    obs = [
        DriftObservation(fork_a="a", fork_b="b", subsystem="low", shared=2, diverged=1,
                         identical=1, only_a=0, only_b=0),
        DriftObservation(fork_a="a", fork_b="b", subsystem="high", shared=9, diverged=8,
                         identical=1, only_a=0, only_b=0),
    ]
    md = render_drift_page("a", "b", obs)
    assert 'title: "Drift: a vs b"' in md
    assert "| Subsystem |" in md
    assert md.index("high") < md.index("low")   # most-diverged first


def test_render_home_shows_counts():
    md = render_home({"reports": 10, "enriched": 7, "open": 3, "likely_fixed": 2,
                      "fixed_confirmed": 1, "drift_pairs": 6})
    assert "**Reports:** 10" in md
    assert "fixed_confirmed 1" in md
    assert "**Drift pairs:** 6" in md
