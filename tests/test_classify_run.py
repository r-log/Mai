from sqlalchemy import func, select

from mai.db.models import Commit, CommitFile, SubsystemClass
from mai.repository.subsystem_class import SubsystemClassRepository
from mai.sync.classify import classify_subsystems


async def _file(session, subsystem, path):
    c = Commit(core="three", sha=f"sha-{subsystem}-{path}", author="a",
               authored_at="t", committer="a", committed_at="t", message="m",
               parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitFile(commit_id=c.id, path=path, change_type="M",
                           added_lines=1, removed_lines=0, subsystem=subsystem))
    await session.flush()


async def test_classifies_distinct_subsystems(session):
    await _file(session, "src/shared/Database", "src/shared/Database/Field.cpp")
    await _file(session, "src/game/Spells", "src/game/Spells/Spell.cpp")
    await _file(session, "src/game/Object", "src/game/Object/Player.cpp")
    await _file(session, "src/game/Object", "src/game/Object/Unit.cpp")  # dup subsystem
    await _file(session, "dep/zlib", "dep/zlib/inflate.c")
    await session.commit()

    result = await classify_subsystems(session)
    assert result["total"] == 4        # four distinct subsystems
    assert result["shared"] == 1 and result["expansion"] == 1
    assert result["mixed"] == 1 and result["vendored"] == 1
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/shared/Database")).classification == "shared"
    assert (await repo.get("src/game/Spells")).classification == "expansion"
    assert (await repo.get("src/game/Object")).classification == "mixed"
    assert (await repo.get("dep/zlib")).classification == "vendored"


async def test_preserves_manual_override(session):
    await _file(session, "src/game/Server", "src/game/Server/WorldSocket.cpp")
    await session.commit()
    await SubsystemClassRepository(session).set_manual("src/game/Server", "shared")
    await session.commit()

    result = await classify_subsystems(session)
    assert result["manual_preserved"] == 1
    assert result["shared"] == 1
    row = await SubsystemClassRepository(session).get("src/game/Server")
    assert row.classification == "shared" and row.source == "manual_override"


async def test_recompute_is_idempotent(session):
    await _file(session, "src/game/Spells", "src/game/Spells/Spell.cpp")
    await session.commit()
    await classify_subsystems(session)
    await classify_subsystems(session)
    assert await session.scalar(select(func.count()).select_from(SubsystemClass)) == 1


async def test_run_reports_client_bound_and_applies_drift_seed(session):
    from mai.db.models import DriftObservation
    await _file(session, "src/game/Server/WorldHandlers",
                "src/game/Server/WorldHandlers/Misc.cpp")        # path -> client_bound
    await _file(session, "src/game/Server", "src/game/Server/WorldSocket.cpp")  # path -> mixed
    session.add(DriftObservation(fork_a="zero", fork_b="one",
                                 subsystem="src/game/Server", shared=5, diverged=5,
                                 identical=0, only_a=0, only_b=0))
    await session.commit()
    result = await classify_subsystems(session)
    assert result["client_bound"] >= 1                 # the WorldHandlers path
    assert result["client_bound_from_drift"] == 1      # Server upgraded by drift
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/game/Server")).classification == "client_bound"
