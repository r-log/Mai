import pytest

from mai.sync.classify import classify_subsystem


@pytest.mark.parametrize("subsystem", [
    "src/shared/Database",
    "src/shared",
    "src/realmd",
    "src/tools/Extractor_projects",
    "src/framework/Threading",
])
def test_shared_infrastructure(subsystem):
    assert classify_subsystem(subsystem) == "shared"


@pytest.mark.parametrize("subsystem", [
    "dep",
    "dep/bzip2",
    "dep/StormLib/src",
    "dep/recastnavigation",
])
def test_vendored_dependencies(subsystem):
    assert classify_subsystem(subsystem) == "vendored"


def test_tools_stays_shared_not_vendored():
    assert classify_subsystem("src/tools/Extractor_projects") == "shared"


@pytest.mark.parametrize("subsystem", [
    "src/game/Spells",
    "src/game/Object/Quests",
    "src/game/BattleGround",
    "src/game/Arena",
    "src/game/Talents",
    "src/game/Loot",
])
def test_expansion_content(subsystem):
    assert classify_subsystem(subsystem) == "expansion"


@pytest.mark.parametrize("subsystem", [
    "src/game/Object",
    "src/game/Server",       # mixes shared socket plumbing + expansion-bound opcode router
    "src/game/Maps",
    "(root)",
])
def test_mixed_default(subsystem):
    assert classify_subsystem(subsystem) == "mixed"


def test_case_insensitive():
    assert classify_subsystem("SRC/SHARED/Log") == "shared"
    assert classify_subsystem("src/game/SPELLS") == "expansion"


def test_dep_prefix_not_confused_by_substring():
    # a path that merely starts with the letters "dep" but isn't the dep/ tree
    assert classify_subsystem("src/game/Dependencies") == "mixed"
