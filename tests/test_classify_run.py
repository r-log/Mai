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
    await session.commit()

    result = await classify_subsystems(session)
    assert result["total"] == 3        # three distinct subsystems
    assert result["shared"] == 1 and result["expansion"] == 1 and result["mixed"] == 1
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/shared/Database")).classification == "shared"
    assert (await repo.get("src/game/Spells")).classification == "expansion"
    assert (await repo.get("src/game/Object")).classification == "mixed"


async def test_preserves_manual_override(session):
    await _file(session, "src/game/Server", "src/game/Server/WorldSocket.cpp")
    await session.commit()
    await SubsystemClassRepository(session).set_manual("src/game/Server", "shared")
    await session.commit()

    result = await classify_subsystems(session)
    assert result["manual_preserved"] == 1
    row = await SubsystemClassRepository(session).get("src/game/Server")
    assert row.classification == "shared" and row.source == "manual_override"


async def test_recompute_is_idempotent(session):
    await _file(session, "src/game/Spells", "src/game/Spells/Spell.cpp")
    await session.commit()
    await classify_subsystems(session)
    await classify_subsystems(session)
    assert await session.scalar(select(func.count()).select_from(SubsystemClass)) == 1
