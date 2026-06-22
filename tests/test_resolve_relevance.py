from mai.sync.verdicts import resolve_relevance


def _files(*subs):
    # (subsystem, added, removed) tuples standing in for CommitFile
    return [type("F", (), {"subsystem": s, "added_lines": a, "removed_lines": r})()
            for s, a, r in subs]


def test_all_shared_is_portable():
    files = _files(("src/shared/Database", 3, 1), ("src/shared/Log", 1, 0))
    classes = {"src/shared/Database": "shared", "src/shared/Log": "shared"}
    relevance, magnitude, rep = resolve_relevance(files, classes)
    assert relevance == "portable"
    assert magnitude == 5
    assert rep == "src/shared/Database"


def test_any_divergent_makes_it_divergent():
    # shared + client_bound in one patch -> NOT cleanly portable -> divergent
    files = _files(("src/shared/Database", 3, 1), ("src/game/Server/Opcodes", 9, 2))
    classes = {"src/shared/Database": "shared", "src/game/Server/Opcodes": "client_bound"}
    relevance, magnitude, rep = resolve_relevance(files, classes)
    assert relevance == "divergent"        # the client_bound touch bars portability


def test_expansion_and_mixed_are_divergent():
    files = _files(("src/game/Spells", 2, 0))
    assert resolve_relevance(files, {"src/game/Spells": "expansion"})[0] == "divergent"
    files = _files(("src/game/Maps", 2, 0))
    assert resolve_relevance(files, {"src/game/Maps": "mixed"})[0] == "divergent"
