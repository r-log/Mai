import pytest

from mai.enrich.schema import (
    EnrichmentInput,
    EnrichmentResult,
    EnrichmentSchemaError,
    parse_enrichment,
    raw_text_from_payload,
)


def test_parse_enrichment_valid_minimal():
    r = parse_enrichment('{"normalized_title":"Pet threat","english_summary":"Pet loses threat."}')
    assert isinstance(r, EnrichmentResult)
    assert r.normalized_title == "Pet threat"
    assert r.needs_human_review is False
    assert r.affected_entities.npc == []
    assert r.clarity_score == 0.0


def test_parse_enrichment_full_dict():
    r = parse_enrichment({
        "normalized_title": "T", "english_summary": "S",
        "steps_to_reproduce": ["a", "b"],
        "affected_entities": {"npc": ["Devilsaur"]},
        "language_detected": "es", "severity_guess": "high",
        "clarity_score": 0.9, "needs_human_review": True,
    })
    assert r.steps_to_reproduce == ["a", "b"]
    assert r.affected_entities.npc == ["Devilsaur"]
    assert r.needs_human_review is True


def test_parse_enrichment_missing_required_raises():
    with pytest.raises(EnrichmentSchemaError):
        parse_enrichment('{"english_summary":"no title field"}')


def test_parse_enrichment_bad_json_raises():
    with pytest.raises(EnrichmentSchemaError):
        parse_enrichment("{not valid json")


def test_enrichment_input_hash_is_stable_and_content_sensitive():
    a = EnrichmentInput("t", "zero", "ips", "body")
    b = EnrichmentInput("t", "zero", "ips", "body")
    c = EnrichmentInput("t", "zero", "ips", "BODY")
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_raw_text_from_payload_picks_right_field():
    assert raw_text_from_payload("ips", {"markdown": "MD"}) == "MD"
    assert raw_text_from_payload("gh_issue", {"body": "BODY"}) == "BODY"
    assert raw_text_from_payload("gh_pr", {"body": None}) == ""
