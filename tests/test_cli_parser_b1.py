from mai.cli.__main__ import build_parser


def test_parser_accepts_user_add():
    args = build_parser().parse_args(["user-add", "antz", "--maintainer"])
    assert args.cmd == "user-add"
    assert args.username == "antz"
    assert args.maintainer is True


def test_parser_user_add_defaults_not_maintainer():
    args = build_parser().parse_args(["user-add", "madmax"])
    assert args.maintainer is False


def test_parser_accepts_user_list_and_serve_web():
    assert build_parser().parse_args(["user-list"]).cmd == "user-list"
    assert build_parser().parse_args(["serve-web"]).cmd == "serve-web"
