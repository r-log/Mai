import asyncio

from sqlalchemy import select

from mai.db.models import PortVerdict, ReviewAdvice
from mai.sync.review import build_review_advice
from mai.sync.verdicts import closeness_label

_BAND_RANK = {"near": 0, "partial": 1}


async def _warm_plan(session, limit: int) -> list[str]:
    """Review items in the near/partial band with no advice row yet, near before
    partial, capped at `limit`. Returns item_ids ('{patch_group_id}:{core}')."""
    rows = (await session.scalars(
        select(PortVerdict).where(PortVerdict.verdict == "review"))).all()
    ranked: list[tuple[int, str]] = []
    for v in rows:
        if not v.conflict_total:
            continue
        band = closeness_label(v.conflict_applied or 0, v.conflict_total)
        if band not in _BAND_RANK:
            continue
        has_row = await session.scalar(select(ReviewAdvice.id).where(
            ReviewAdvice.patch_group_id == v.patch_group_id, ReviewAdvice.core == v.core))
        if has_row is not None:
            continue
        ranked.append((_BAND_RANK[band], f"{v.patch_group_id}:{v.core}"))
    ranked.sort(key=lambda t: t[0])
    return [item_id for _, item_id in ranked[:limit]]


async def warm_advice(session_factory, git_client, judge, *, limit: int = 200,
                      concurrency: int = 4) -> dict:
    """Pre-compute advisor opinions over the near/partial review backlog. Bounded,
    concurrent, resumable: each item runs build_review_advice in its OWN session
    (LLM calls overlap; each commits its own cache row). A failed item is skipped."""
    async with session_factory() as session:
        plan = await _warm_plan(session, limit)

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counts = {"planned": len(plan), "warmed": 0, "failed": 0}

    async def worker(item_id: str) -> None:
        async with sem:
            try:
                async with session_factory() as s:
                    out = await build_review_advice(s, git_client, judge, item_id)
            except Exception:  # noqa: BLE001 — one bad item must not abort the batch
                async with lock:
                    counts["failed"] += 1
                return
        async with lock:
            if out.get("opinion") is not None:
                counts["warmed"] += 1
            else:
                counts["failed"] += 1

    await asyncio.gather(*(worker(i) for i in plan))
    return counts
