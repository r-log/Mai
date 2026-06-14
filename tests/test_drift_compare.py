from mai.drift.compare import compare_trees, subsystem_of


def test_subsystem_of_groups_by_directory_depth():
    assert subsystem_of("src/game/Object/Player.cpp", 3) == "src/game/Object"
    assert subsystem_of("src/game/Object/Item/Item.cpp", 3) == "src/game/Object"
    assert subsystem_of("src/shared/Log.cpp", 3) == "src/shared"
    assert subsystem_of("README.md", 3) == "(root)"


def test_compare_trees_counts_identical_diverged_and_unique():
    a = {
        "src/game/Object/Player.cpp": "sha_p1",
        "src/game/Object/Unit.cpp": "sha_u",
        "src/shared/Log.cpp": "sha_l",
        "OnlyA.txt": "sha_a",
    }
    b = {
        "src/game/Object/Player.cpp": "sha_p2",   # diverged
        "src/game/Object/Unit.cpp": "sha_u",      # identical
        "src/shared/Log.cpp": "sha_l",            # identical
        "OnlyB.txt": "sha_b",
    }
    stats = compare_trees(a, b, depth=3)
    obj = stats["src/game/Object"]
    assert (obj["shared"], obj["diverged"], obj["identical"]) == (2, 1, 1)
    shared = stats["src/shared"]
    assert (shared["shared"], shared["identical"]) == (1, 1)
    root = stats["(root)"]
    assert (root["only_a"], root["only_b"]) == (1, 1)
