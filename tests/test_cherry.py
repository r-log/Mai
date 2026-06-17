from mai.sync.cherry import parse_cherry_sources


def test_parses_single_cherry_trailer():
    msg = "Fix pet threat\n\n(cherry picked from commit a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0)"
    assert parse_cherry_sources(msg) == ["a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"]


def test_parses_multiple_and_dedupes_preserving_order():
    msg = ("port\n\n(cherry picked from commit aaaaaaa)\n"
           "(cherry picked from commit bbbbbbb)\n"
           "(cherry picked from commit aaaaaaa)")
    assert parse_cherry_sources(msg) == ["aaaaaaa", "bbbbbbb"]


def test_case_insensitive_and_short_sha():
    assert parse_cherry_sources("x\n(Cherry picked from commit ABC1234)") == ["ABC1234"]


def test_no_trailer_returns_empty():
    assert parse_cherry_sources("just a normal commit message") == []


def test_none_or_empty_message_is_safe():
    assert parse_cherry_sources("") == []
    assert parse_cherry_sources(None) == []
