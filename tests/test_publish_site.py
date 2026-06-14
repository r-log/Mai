from pathlib import Path

from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.publish.site import publish_site
from mai.repository.drift import DriftRepository

STATS = {"shared": 5, "diverged": 3, "identical": 2, "only_a": 0, "only_b": 1}


async def test_publish_site_writes_home_bug_and_drift(session, tmp_path):
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "zero",
                                            raw_payload={"markdown": "x"}))
    await DriftRepository(session).upsert("mangoszero/server", "mangostwo/server",
                                          "src/game/Object", STATS)
    await session.commit()
    written = await publish_site(session, str(tmp_path))
    content = tmp_path / "content"
    assert (content / "_index.md").exists()
    bug = content / "zero" / "bugs" / "ips-r1.md"
    assert bug.exists()
    assert "Pet bug" in bug.read_text(encoding="utf-8")
    drift = content / "sync" / "mangoszero-server--vs--mangostwo-server.md"
    assert drift.exists()
    assert "src/game/Object" in drift.read_text(encoding="utf-8")
    assert written == 3  # home + 1 bug + 1 drift


async def test_publish_site_excludes_pr_reports(session, tmp_path):
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    await publish_site(session, str(tmp_path))
    # only the home page is written; the PR is not a bug page
    assert not (tmp_path / "content" / "zero" / "bugs").exists()
