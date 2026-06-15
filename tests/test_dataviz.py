from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.publish.dataviz import build_dashboard, build_drift_matrix, heat_hex
from mai.repository.correlation import VerificationRepository
from mai.repository.drift import DriftRepository
from mai.repository.reports import ReportRepository



def test_heat_hex_is_hex_and_redder_when_higher():
    assert heat_hex(70).startswith("#") and len(heat_hex(70)) == 7
    r_lo, r_hi = int(heat_hex(58)[1:3], 16), int(heat_hex(88)[1:3], 16)
    g_lo, g_hi = int(heat_hex(58)[3:5], 16), int(heat_hex(88)[3:5], 16)
    assert r_hi >= r_lo and g_hi <= g_lo   # higher % -> more red, less green


async def test_build_drift_matrix_aggregates_and_colors(session):
    d = DriftRepository(session)
    await d.upsert("mangoszero/server", "mangostwo/server", "src/game/Object",
                   {"shared": 80, "diverged": 60, "identical": 20, "only_a": 0, "only_b": 0})
    await d.upsert("mangoszero/server", "mangostwo/server", "src/shared",
                   {"shared": 20, "diverged": 4, "identical": 16, "only_a": 0, "only_b": 0})
    await session.commit()
    m = await build_drift_matrix(session)
    assert set(m["cores"]) == {"Zero", "Two"}
    cells = [c for row in m["rows"] for c in row["cells"] if not c.get("self")]
    vals = [c["value"] for c in cells if c.get("value") is not None]
    assert 64 in vals   # (60+4)/(80+20) = 64 %
    assert all(c["color"].startswith("#") for c in cells if c.get("value") is not None)
    # diagonal is a self cell
    assert any(c.get("self") for row in m["rows"] for c in row["cells"])


async def test_build_drift_matrix_keeps_zero_divergence_as_value(session):
    d = DriftRepository(session)
    await d.upsert("mangoszero/server", "mangostwo/server", "src/shared",
                   {"shared": 10, "diverged": 0, "identical": 10, "only_a": 0, "only_b": 0})
    await session.commit()
    m = await build_drift_matrix(session)
    cells = [c for row in m["rows"] for c in row["cells"] if not c.get("self")]
    assert any(c.get("value") == 0 for c in cells)  # 0% is a real value, not None


async def test_build_dashboard_summarizes(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "three",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "mangosthree/server#7", "Fix", "three",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    bug = await ReportRepository(session).get_report("ips:r1")
    await VerificationRepository(session).upsert(
        bug.id, "fixed_confirmed", 0.95, [{"related": "gh_pr:mangosthree/server#7"}])
    await session.commit()
    dash = await build_dashboard(session)
    assert dash["stats"]["reports"] == 2
    assert dash["stats"]["fixed_confirmed"] == 1
    assert any(a["name"] == "Creature" for a in dash["top_areas"])   # "Pet bug" -> Creature
    rf = dash["recently_fixed"][0]
    assert rf["id"] == "ips:r1"
    assert rf["related"] == "gh_pr:mangosthree/server#7"
    assert rf["url"] == "/three/bugs/ips-r1/"


async def test_build_frequency_heightfield(session):
    from mai.publish.dataviz import build_frequency
    d = DriftRepository(session)
    await d.upsert("mangoszero/server", "mangostwo/server", "src/game/Object",
                   {"shared": 80, "diverged": 60, "identical": 20, "only_a": 0, "only_b": 0})
    await d.upsert("mangoszero/server", "mangostwo/server", "src/shared",
                   {"shared": 40, "diverged": 10, "identical": 30, "only_a": 0, "only_b": 0})
    await session.commit()
    f = await build_frequency(session)
    assert {c["name"] for c in f["cores"]} == {"Zero", "Two"}
    assert all("y" in c and "full" in c for c in f["cores"])
    names = {s["name"] for s in f["subsystems"]}
    assert "Object" in names and "shared" in names      # last path segment
    assert all("x" in s and "z" in s for s in f["subsystems"])
    zero_full = next(c["full"] for c in f["cores"] if c["name"] == "Zero")
    assert f["intensity"][zero_full]["src/game/Object"] == 1.125   # 60/80 * 1.5


async def test_build_frequency_empty_db_is_empty(session):
    from mai.publish.dataviz import build_frequency
    f = await build_frequency(session)
    assert f["cores"] == [] and f["subsystems"] == [] and f["intensity"] == {}


async def test_write_dataviz_writes_three_files(session, tmp_path):
    from mai.publish.dataviz import write_dataviz
    await DriftRepository(session).upsert(
        "mangoszero/server", "mangostwo/server", "src/game/Object",
        {"shared": 10, "diverged": 5, "identical": 5, "only_a": 0, "only_b": 0})
    await session.commit()
    await write_dataviz(session, str(tmp_path))
    for name in ("drift.json", "dashboard.json", "frequency.json"):
        assert (tmp_path / "data" / name).exists()


async def test_build_dashboard_coverage(session):
    from mai.publish.dataviz import build_dashboard
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "three",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("ips", "r2", "Spell bug", "zero",
                                            raw_payload={"markdown": "y"}))
    await ingest_event(session, IntakeEvent("ips", "r3", "Another bug", "three",
                                            raw_payload={"markdown": "z"}))
    await session.commit()
    dash = await build_dashboard(session)
    cov = dash["coverage"]
    assert cov["enriched"] == 0 and cov["total"] == 3
    assert {c["core"]: c["reports"] for c in cov["cores"]} == {"three": 2, "zero": 1}
    assert cov["cores"][0]["core"] == "three"   # sorted descending by count
    assert isinstance(cov["generated_at"], str) and "T" in cov["generated_at"]
    assert cov["generated_at"].endswith("+00:00") or cov["generated_at"].endswith("Z")
