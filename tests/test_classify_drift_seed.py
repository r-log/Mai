from mai.db.models import DriftObservation, SubsystemClass
from mai.repository.subsystem_class import SubsystemClassRepository
from mai.sync.classify import seed_client_bound_from_drift


async def _drift(session, sub, fork_b, *, identical, diverged):
    session.add(DriftObservation(fork_a="zero", fork_b=fork_b, subsystem=sub,
                                 shared=identical + diverged, diverged=diverged,
                                 identical=identical, only_a=0, only_b=0))


async def _mixed(session, sub):
    session.add(SubsystemClass(subsystem=sub, classification="mixed", source="heuristic"))


async def test_fully_diverged_mixed_becomes_client_bound(session):
    await _mixed(session, "src/game/Server")
    await _drift(session, "src/game/Server", "one", identical=0, diverged=9)
    await _drift(session, "src/game/Server", "two", identical=0, diverged=7)
    await session.commit()
    n = await seed_client_bound_from_drift(session)
    assert n == 1
    assert (await SubsystemClassRepository(session).get("src/game/Server")
            ).classification == "client_bound"


async def test_partly_identical_is_not_upgraded(session):
    await _mixed(session, "src/game/Maps")
    await _drift(session, "src/game/Maps", "one", identical=0, diverged=4)
    await _drift(session, "src/game/Maps", "two", identical=3, diverged=1)  # matches in one pair
    await session.commit()
    n = await seed_client_bound_from_drift(session)
    assert n == 0
    assert (await SubsystemClassRepository(session).get("src/game/Maps")
            ).classification == "mixed"


async def test_does_not_override_confident_or_manual(session):
    # a path-confident 'shared' subsystem that happens to be fully diverged is NOT demoted
    session.add(SubsystemClass(subsystem="src/shared/Foo", classification="shared",
                               source="heuristic"))
    session.add(SubsystemClass(subsystem="src/game/Bar", classification="mixed",
                               source="manual_override"))
    await _drift(session, "src/shared/Foo", "one", identical=0, diverged=5)
    await _drift(session, "src/game/Bar", "one", identical=0, diverged=5)
    await session.commit()
    n = await seed_client_bound_from_drift(session)
    assert n == 0
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/shared/Foo")).classification == "shared"
    assert (await repo.get("src/game/Bar")).classification == "mixed"
