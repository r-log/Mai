# tests/test_ground_opinion.py
from mai.judge.schema import ReviewOpinion, AdaptedHunk
from mai.judge.ground import ground_opinion

EVIDENCE = {
    "fix": {"source_sha": "abc123def456"},
    "conflict": {"hunks": [{"path": "src/x.cpp", "target_line": 65,
                            "patch_text": "@@", "target_context": "code"}]},
    "similar": [{"sha": "deadbeef0099"}],
}


def test_grounded_claim_survives_with_proportional_confidence():
    op = ReviewOpinion(assessment="portable", confidence=0.8, reason="ok",
                       tips=["rename in src/x.cpp"],          # grounded (path)
                       citations=["src/x.cpp:65", "totally/unseen.cpp"],  # 1 grounded, 1 not
                       adapted_hunks=[AdaptedHunk(path="src/x.cpp", suggestion="use Close()")])
    out = ground_opinion(op, EVIDENCE)
    assert "rename in src/x.cpp" in out.tips
    assert out.citations == ["src/x.cpp:65"]                 # ungrounded citation dropped
    assert len(out.adapted_hunks) == 1
    # 3 kept of 4 total -> 0.8 * 0.75 = 0.6
    assert out.confidence == 0.6
    assert out.assessment == "portable"


def test_all_ungrounded_forces_uncertain_zero():
    op = ReviewOpinion(assessment="portable", confidence=0.9, reason="looks fine",
                       tips=["edit some/other.cpp"], citations=["nope.cpp"])
    out = ground_opinion(op, EVIDENCE)
    assert out.assessment == "uncertain"
    assert out.confidence == 0.0
    assert out.tips == [] and out.citations == []
    assert "ungrounded" in out.reason.lower()


def test_no_claims_keeps_confidence_unchanged():
    op = ReviewOpinion(assessment="divergent", confidence=0.5, reason="differs")
    out = ground_opinion(op, EVIDENCE)
    assert out.assessment == "divergent"
    assert out.confidence == 0.5            # nothing to verify -> fraction 1.0
