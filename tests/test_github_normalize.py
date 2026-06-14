from mai.contracts import IntakeEvent
from mai.github.normalize import normalize_issue, normalize_pull

ISSUE = {"number": 5, "title": "Crash on login", "state": "open",
         "updated_at": "2026-03-01T00:00:00Z", "body": "boom"}
PR_IN_ISSUES = {"number": 9, "title": "Fix crash", "state": "closed",
                "updated_at": "2026-03-02T00:00:00Z", "pull_request": {"url": "x"}}
PR_MERGED = {"number": 12, "title": "Fix threat", "state": "closed",
             "merged_at": "2026-03-03T00:00:00Z", "updated_at": "2026-03-03T00:00:00Z"}
PR_OPEN = {"number": 13, "title": "WIP", "state": "open",
           "merged_at": None, "updated_at": "2026-03-04T00:00:00Z"}


def test_normalize_issue_maps_fields():
    evt = normalize_issue("mangoszero/server", "zero", ISSUE)
    assert isinstance(evt, IntakeEvent)
    assert evt.source_type == "gh_issue"
    assert evt.source_id == "mangoszero/server#5"
    assert evt.canonical_key() == "gh_issue:mangoszero/server#5"
    assert evt.core == "zero"
    assert evt.status == "open"
    assert evt.repo_full_name == "mangoszero/server"
    assert evt.raw_payload == ISSUE


def test_normalize_issue_returns_none_for_pull_request():
    assert normalize_issue("mangoszero/server", "zero", PR_IN_ISSUES) is None


def test_normalize_pull_merged_status():
    evt = normalize_pull("mangoszero/server", "zero", PR_MERGED)
    assert evt.source_type == "gh_pr"
    assert evt.source_id == "mangoszero/server#12"
    assert evt.canonical_key() == "gh_pr:mangoszero/server#12"
    assert evt.status == "merged"


def test_normalize_pull_open_status():
    assert normalize_pull("mangoszero/server", "zero", PR_OPEN).status == "open"
