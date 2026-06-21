from mai.cli.__main__ import build_parser


def test_parser_accepts_refresh():
    assert build_parser().parse_args(["refresh"]).cmd == "refresh"


def test_parser_accepts_serve():
    assert build_parser().parse_args(["serve"]).cmd == "serve"


def test_parser_still_accepts_existing_command():
    assert build_parser().parse_args(["sync-analyze"]).cmd == "sync-analyze"
