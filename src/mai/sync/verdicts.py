from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, Propagation
from mai.repository.port_candidate import magnitude_tier
from mai.repository.port_verdict import PortVerdictRepository
from mai.repository.subsystem_class import SubsystemClassRepository


def resolve_relevance(files, classes: dict[str, str]) -> tuple[str, int, str]:
    """Resolve a fix's portability from its touched files + subsystem classes.

    portable iff EVERY touched subsystem is classified 'shared' (a patch that also
    touches client_bound/expansion/vendored/mixed code cannot be a clean cross-port).
    magnitude = all touched lines in both cases.
    Returns (relevance, magnitude, representative_subsystem).
    """
    touched = sorted({f.subsystem for f in files})
    all_shared = bool(touched) and all(classes.get(s) == "shared" for s in touched)
    magnitude = sum(f.added_lines + f.removed_lines for f in files)
    if all_shared:
        return "portable", magnitude, touched[0]
    return "divergent", magnitude, (touched[0] if touched else "(root)")


async def compute_verdicts(session: AsyncSession, git_client) -> dict:
    """For each fix x each non-present core, grade applicability x relevance into a
    PortVerdict. NEEDS only when the patch applies cleanly AND every touched
    subsystem is shared. Incremental: cached on (source_sha, base_sha). Offline DB +
    git worktrees; no network."""
    rows = (await session.execute(
        select(Propagation.patch_group_id, Propagation.core,
               Propagation.present, Propagation.source_sha))).all()
    groups: dict[str, dict[str, list]] = defaultdict(
        lambda: {"present": [], "absent": []})
    for r in rows:
        groups[r.patch_group_id]["present" if r.present else "absent"].append(
            (r.core, r.source_sha))

    vrepo = PortVerdictRepository(session)
    sc_repo = SubsystemClassRepository(session)
    counts = {"needs": 0, "review": 0, "not_applicable": 0, "has_it": 0,
              "cached": 0, "recomputed": 0, "errors": 0}
    head_cache: dict[str, str] = {}

    for pg_id, gd in groups.items():
        if not gd["present"] or not gd["absent"]:
            continue
        source_core, source_sha = min(gd["present"], key=lambda t: t[0])
        if source_sha is None:
            continue
        commit = await session.scalar(
            select(Commit).where(Commit.core == source_core, Commit.sha == source_sha))
        if commit is None:
            continue
        files = list(await session.scalars(
            select(CommitFile).where(CommitFile.commit_id == commit.id)))
        if not files:
            continue
        classes = {f.subsystem: (await sc_repo.get(f.subsystem)) for f in files}
        classes = {s: (c.classification if c else "mixed") for s, c in classes.items()}
        relevance, magnitude, rep = resolve_relevance(files, classes)
        paths = sorted({f.path for f in files})
        patch: str | None = None

        for target_core, _ in gd["absent"]:
            # A git error on one (fix, core) must not abort the whole batch.
            try:
                if target_core not in head_cache:
                    head_cache[target_core] = await git_client.head_sha(target_core)
                base = head_cache[target_core]
                existing = await vrepo.get(pg_id, target_core)
                if (existing is not None and existing.source_sha == source_sha
                        and existing.base_sha == base):
                    counts["cached"] += 1
                    counts[existing.verdict] = counts.get(existing.verdict, 0) + 1
                    continue

                if patch is None:
                    patch = await git_client.diff(source_core, source_sha)
                exists = await git_client.paths_exist(target_core, paths)
                if not any(exists.values()):
                    verdict, apply_result = "not_applicable", "file_absent"
                elif await git_client.apply_check(target_core, patch, reverse=True) == "reverse_clean":
                    verdict, apply_result = "has_it", "reverse_clean"
                else:
                    apply_result = await git_client.apply_check(target_core, patch)
                    if apply_result == "clean":
                        verdict = "needs" if relevance == "portable" else "review"
                    elif apply_result == "file_absent":
                        verdict = "not_applicable"
                    else:
                        verdict = "review"

                confidence = "high" if verdict in ("needs", "has_it") else "medium"
                evidence = [f"source {source_core}@{source_sha}",
                            f"apply {apply_result}", f"relevance {relevance} ({rep})",
                            f"absent-by-patch-id in {target_core}"]
                await vrepo.upsert(
                    pg_id, target_core, verdict=verdict, apply_result=apply_result,
                    relevance=relevance, source_core=source_core, source_sha=source_sha,
                    base_sha=base, subsystem=rep, magnitude=magnitude,
                    tier=magnitude_tier(magnitude), confidence=confidence,
                    similar_commit=None, evidence=evidence)
                counts["recomputed"] += 1
                counts[verdict] = counts.get(verdict, 0) + 1
            except Exception:  # noqa: BLE001 - batch derivation; record + continue
                counts["errors"] += 1
                continue

    await session.commit()
    return counts
