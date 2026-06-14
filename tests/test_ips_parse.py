from pathlib import Path

from mai.ips.parse import parse_bug_page, parse_bug_url

FIXTURE = (Path(__file__).parent / "fixtures" / "ips_bug_r1842.md").read_text(encoding="utf-8")
URL = ("https://www.getmangos.eu/bug-tracker/mangos-zero/"
       "agro-from-pet-doesnt-work-as-expected-r1842/")


def test_parse_bug_url_extracts_core_and_id():
    assert parse_bug_url(URL) == ("zero", "r1842")


def test_parse_bug_url_handles_nested_cross_core():
    url = ("https://www.getmangos.eu/bug-tracker/cross-core/sub-modules/scriptdev3/"
           "script-error-in-npc_prospector_anvilward-r1828/")
    assert parse_bug_url(url) == ("cross-core", "r1828")


def test_parse_bug_url_rejects_non_bug_url():
    import pytest
    with pytest.raises(ValueError):
        parse_bug_url("https://www.getmangos.eu/bug-tracker/mangos-zero/")


def test_parse_bug_page_extracts_fields():
    bug = parse_bug_page(FIXTURE)
    assert bug.title == "Agro from pet doesnt work as expected"
    assert bug.status == "Completed"
    assert bug.main_category == "Core / Mangos Daemon"
    assert bug.sub_category == "Pet"
    assert bug.version == "22.x (Current Master Branch)"
    assert bug.milestone == "Unset"
    assert bug.priority == "New"
    assert bug.implemented_version == "Unset"
