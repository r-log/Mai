import pytest
from mai.judge.schema import ReviewOpinion, parse_opinion, ReviewOpinionSchemaError


def test_parse_opinion_validates_a_good_object():
    op = parse_opinion('{"assessment":"portable","confidence":0.7,"reason":"clean",'
                        '"tips":["adapt Close() in src/x.cpp"],"citations":["src/x.cpp"]}')
    assert isinstance(op, ReviewOpinion)
    assert op.assessment == "portable"
    assert op.confidence == 0.7
    assert op.tips == ["adapt Close() in src/x.cpp"]
    assert op.adapted_hunks == []          # defaults to empty


def test_parse_opinion_rejects_bad_enum():
    with pytest.raises(ReviewOpinionSchemaError):
        parse_opinion('{"assessment":"maybe","confidence":0.5,"reason":"x"}')


def test_parse_opinion_rejects_out_of_range_confidence():
    with pytest.raises(ReviewOpinionSchemaError):
        parse_opinion('{"assessment":"portable","confidence":2.0,"reason":"x"}')


def test_parse_opinion_rejects_invalid_json():
    with pytest.raises(ReviewOpinionSchemaError):
        parse_opinion("{not json")


def test_parse_opinion_strips_json_code_fence():
    # Claude/Gemini wrap JSON in a ```json fence even under response_format=json_object
    op = parse_opinion('```json\n{"assessment":"divergent","confidence":0.4,"reason":"d"}\n```')
    assert op.assessment == "divergent"
    assert op.confidence == 0.4


def test_parse_opinion_strips_bare_code_fence():
    op = parse_opinion('```\n{"assessment":"portable","confidence":0.6,"reason":"ok"}\n```')
    assert op.assessment == "portable"


def test_parse_opinion_extracts_json_after_prose():
    # Real-world: Claude ignores response_format and emits reasoning, THEN the fenced JSON.
    raw = ('Looking at the evidence:\n1. The target already has it.\n\n'
           '```json\n{"assessment":"already_handled","confidence":0.97,'
           '"reason":"already in target"}\n```')
    op = parse_opinion(raw)
    assert op.assessment == "already_handled"
    assert op.confidence == 0.97


def test_parse_opinion_extracts_raw_object_after_prose():
    # No fence, prose then a raw object.
    op = parse_opinion('Here is my answer: {"assessment":"divergent","confidence":0.3,"reason":"x"}')
    assert op.assessment == "divergent"
