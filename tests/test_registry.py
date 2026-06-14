from pathlib import Path

from mai.sources.registry import parse_registry
from mai.repository.repos import RepoRepository

FIXTURE = Path(__file__).parent / "fixtures" / "mangos_readme.md"


def test_parse_registry_extracts_unique_github_repos():
    rows = parse_registry(FIXTURE.read_text())
    full_names = [r.full_name for r in rows]
    assert full_names == ["mangosthree/server", "mangostwo/server", "mangoszero/server"]
    assert {r.core for r in rows} == {"zero", "two", "three"}


async def test_registry_rows_upsert_idempotently(session):
    repo_repo = RepoRepository(session)
    for row in parse_registry(FIXTURE.read_text()):
        await repo_repo.upsert(row.full_name, row.core, row.url)
    await session.commit()
    for row in parse_registry(FIXTURE.read_text()):
        await repo_repo.upsert(row.full_name, row.core, row.url)
    await session.commit()
    assert len(await repo_repo.all()) == 3
