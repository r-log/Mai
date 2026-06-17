from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, PatchGroup, Propagation
from mai.repository.port_candidate import PortCandidateRepository, magnitude_tier
from mai.repository.subsystem_class import SubsystemClassRepository


async def compute_port_candidates(session: AsyncSession) -> dict:
    """Synthesize port-debt from the propagation matrix + subsystem classes.

    For every fix (PatchGroup) present in >=1 fork and absent in >=1 fork, whose
    source commit touches a `shared` subsystem, emit one PortCandidate per absent
    target. Candidates whose target later acquires the fix auto-resolve to 'ported'.
    Human `status` (dismissed/ported) is preserved across recompute. Offline.
    """
    rows = (await session.execute(
        select(PatchGroup.id, Propagation.core, Propagation.present,
               Propagation.source_sha)
        .join(Propagation, Propagation.patch_group_id == PatchGroup.id)
    )).all()

    groups: dict[str, dict[str, list]] = defaultdict(lambda: {"present": [], "absent": []})
    for r in rows:
        bucket = "present" if r.present else "absent"
        groups[r.id][bucket].append((r.core, r.source_sha))

    cand_repo = PortCandidateRepository(session)
    sc_repo = SubsystemClassRepository(session)
    current: set[tuple[str, str]] = set()
    skipped = 0

    for pg_id, gd in groups.items():
        if not gd["present"] or not gd["absent"]:
            continue
        source_core, source_sha = min(gd["present"], key=lambda t: t[0])
        if source_sha is None:
            continue
        commit = await session.scalar(
            select(Commit).where(Commit.core == source_core, Commit.sha == source_sha)
        )
        if commit is None:
            continue
        files = list(await session.scalars(
            select(CommitFile).where(CommitFile.commit_id == commit.id)
        ))
        touched = sorted({f.subsystem for f in files})

        shared_subs = []
        for sub in touched:
            sc = await sc_repo.get(sub)
            if sc is not None and sc.classification == "shared":
                shared_subs.append(sub)
        if not shared_subs:
            skipped += 1
            continue

        # Magnitude counts only the PORTABLE (shared-subsystem) lines, so a big
        # mixed commit (a vendoring blob that also nudges a shared file) is sized
        # by its actual portable change, not the whole commit.
        shared_set = set(shared_subs)
        magnitude = sum(f.added_lines + f.removed_lines
                        for f in files if f.subsystem in shared_set)

        rep = shared_subs[0]
        absent_cores = sorted(c for c, _ in gd["absent"])
        evidence = [
            f"present in {source_core}@{source_sha}",
            f"shared subsystem {rep}",
            f"absent in {', '.join(absent_cores)}",
        ]
        for target_core in absent_cores:
            await cand_repo.upsert(
                pg_id, target_core, source_core=source_core, subsystem=rep,
                classification="shared", magnitude=magnitude, confidence="high",
                evidence=evidence, source_sha=source_sha)
            current.add((pg_id, target_core))

    auto_resolved = 0
    for cand in await cand_repo.open_candidates():
        if (cand.patch_group_id, cand.target_core) not in current:
            await cand_repo.mark_status(cand, "ported")
            auto_resolved += 1

    open_now = await cand_repo.open_candidates()
    tiers = {"surgical": 0, "small": 0, "moderate": 0, "bulk": 0}
    for c in open_now:
        tiers[magnitude_tier(c.magnitude)] += 1
    await session.commit()
    return {"candidates": len(open_now), "skipped_unportable": skipped,
            "auto_resolved": auto_resolved, "tiers": tiers}
