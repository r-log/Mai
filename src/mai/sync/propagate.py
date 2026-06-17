from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitPatch
from mai.repository.propagation import PatchGroupRepository, PropagationRepository
from mai.sync.cherry import parse_cherry_sources


async def compute_propagation(session: AsyncSession) -> dict:
    """Group commits across forks by patch-id and record per-fork presence.

    Reads stored Commit/CommitPatch rows (offline), builds a present/absent matrix
    keyed by (patch_id, core), augments it with cherry-pick-trailer links, and
    persists PatchGroup + Propagation idempotently. Recomputable.
    """
    rows = (await session.execute(
        select(Commit.core, Commit.sha, CommitPatch.patch_id, Commit.message)
        .join(CommitPatch, CommitPatch.commit_id == Commit.id)
        .where(CommitPatch.patch_id.is_not(None))
    )).all()

    tracked = sorted({r.core for r in rows})
    sha_to_patch = {r.sha: r.patch_id for r in rows}

    # patch_id -> {core: first sha seen}
    present_by_patch: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        present_by_patch[r.patch_id].setdefault(r.core, r.sha)

    patch_ids = sorted(present_by_patch)
    # matrix[(patch_id, core)] = {present, vias:set, sha, evidence:list}
    matrix: dict[tuple[str, str], dict] = {}
    for pid in patch_ids:
        for core in tracked:
            if core in present_by_patch[pid]:
                sha = present_by_patch[pid][core]
                matrix[(pid, core)] = {"present": True, "vias": {"patch_id"},
                                       "sha": sha,
                                       "evidence": [f"patch_id {pid} in {core}@{sha}"]}
            else:
                matrix[(pid, core)] = {"present": False, "vias": set(),
                                       "sha": None, "evidence": []}

    cherry_links = 0
    for r in rows:
        for src in parse_cherry_sources(r.message):
            src_pid = sha_to_patch.get(src)
            if src_pid is None:
                continue
            cell = matrix.get((src_pid, r.core))
            if cell is None:
                continue
            if not cell["present"]:
                cell["present"] = True
                cell["sha"] = r.sha
            cell["vias"].add("cherry_trailer")
            cell["evidence"].append(f"cherry-trail {r.core}@{r.sha} <- {src}")
            cherry_links += 1

    pg_repo = PatchGroupRepository(session)
    prop_repo = PropagationRepository(session)
    n_present = n_absent = 0
    for pid in patch_ids:
        pg = await pg_repo.get_or_create(pid)
        for core in tracked:
            cell = matrix[(pid, core)]
            via = "+".join(sorted(cell["vias"])) if cell["vias"] else None
            await prop_repo.upsert(pg.id, core, present=cell["present"], via=via,
                                   confidence="high", source_sha=cell["sha"],
                                   evidence=cell["evidence"])
            if cell["present"]:
                n_present += 1
            else:
                n_absent += 1

    await session.commit()
    return {"groups": len(patch_ids), "present": n_present,
            "absent": n_absent, "cherry_links": cherry_links}
