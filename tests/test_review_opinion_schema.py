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
