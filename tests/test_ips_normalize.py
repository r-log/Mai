from pathlib import Path

from mai.contracts import IntakeEvent
from mai.ips.normalize import normalize_ips

FIXTURE = (Path(__file__).parent / "fixtures" / "ips_bug_r1842.md").read_text(encoding="utf-8")
URL = ("https://www.getmangos.eu/bug-tracker/mangos-zero/"
       "agro-from-pet-doesnt-work-as-expected-r1842/")


def test_normalize_ips_builds_intake_event():
    evt = normalize_ips(URL, FIXTURE)
    assert isinstance(evt, IntakeEvent)
    assert evt.source_type == "ips"
    assert evt.source_id == "r1842"
    assert evt.canonical_key() == "ips:r1842"
    assert evt.core == "zero"
    assert evt.status == "completed"  # lowercased source status
    assert evt.title == "Agro from pet doesnt work as expected"
    assert evt.repo_full_name is None


def test_normalize_ips_preserves_raw_and_parsed_fields():
    evt = normalize_ips(URL, FIXTURE)
    assert evt.raw_payload["url"] == URL
    assert evt.raw_payload["markdown"] == FIXTURE  # full page preserved (raw is sacred)
    assert evt.raw_payload["sub_category"] == "Pet"
    assert evt.raw_payload["priority"] == "New"
