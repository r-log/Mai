from mai.cli.__main__ import build_parser


def test_warm_advice_args_parse():
    args = build_parser().parse_args(["warm-advice", "--limit", "50", "--concurrency", "2"])
    assert args.cmd == "warm-advice" and args.limit == 50 and args.concurrency == 2


def test_warm_advice_defaults():
    args = build_parser().parse_args(["warm-advice"])
    assert args.limit == 200 and args.concurrency == 4
