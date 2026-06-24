"""Personal Control Center data under the simplified one-action model: a human
only ever *takes* a task (claim/assign); the engine auto-resolves it when the
port lands (BoardItem.archived via reconcile) — no manual close/link/done.

Surfaces: My todo (taken, not yet landed) · Shipped (auto-resolved) · Available
to grab · activity feed · small team + project overview. Reads BoardItem
(state) + BoardEvent (history) + PortVerdict (fix metadata). Pure read."""
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import BoardEvent, BoardItem, Commit, PortVerdict


async def _fix_index(session: AsyncSession) -> dict[str, dict]:
    """item_id (`{patch_group}:{core}`) -> its fix metadata, for every verdict."""
    idx: dict[str, dict] = {}
    for v in await session.scalars(select(PortVerdict)):
        idx[f"{v.patch_group_id}:{v.core}"] = {
            "core": v.core, "subsystem": v.subsystem, "verdict": v.verdict,
            "source_core": v.source_core, "source_sha": v.source_sha}
    return idx


async def build_me_dashboard(session: AsyncSession, username: str, *,
                             repos: dict[str, str] | None = None) -> dict:
    """Everything the /me cockpit shows for `username`."""
    items = list(await session.scalars(select(BoardItem)))
    events = list(await session.scalars(select(BoardEvent)))
    idx = await _fix_index(session)
    title_cache: dict[tuple, str] = {}

    async def resolve(item_id: str) -> dict:
        fi = idx.get(item_id, {})
        key = (fi.get("source_core"), fi.get("source_sha"))
        if key not in title_cache:
            t = ""
            if key[1]:
                c = await session.scalar(select(Commit).where(
                    Commit.core == key[0], Commit.sha == key[1]))
                if c and c.message:
                    t = c.message.strip().splitlines()[0]
            title_cache[key] = t
        url = None
        if repos and fi.get("source_core") and fi.get("source_sha"):
            r = repos.get(fi["source_core"])
            if r:
                url = f"https://github.com/{r}/commit/{fi['source_sha']}"
        return {"core": fi.get("core", ""), "subsystem": fi.get("subsystem", ""),
                "title": title_cache[key] or item_id, "source_url": url}

    active = [b for b in items if b.assignee == username and not b.archived]
    shipped_items = [b for b in items if b.assignee == username and b.archived]
    self_ids = {e.port_candidate_id for e in events
                if e.actor == username and e.action == "claim"}
    assigned_ids = {e.port_candidate_id for e in events
                    if e.action == "assign" and e.to_value == username}

    def via(item_id: str) -> str:
        return "assigned" if (item_id in assigned_ids and item_id not in self_ids) else "self"

    queue = []
    for b in sorted(active, key=lambda b: b.updated_at, reverse=True):
        info = await resolve(b.port_candidate_id)
        queue.append({**info, "item_id": b.port_candidate_id,
                      "via": via(b.port_candidate_id), "since": b.updated_at.isoformat()})

    shipped = []
    for b in sorted(shipped_items, key=lambda b: b.updated_at, reverse=True)[:30]:
        info = await resolve(b.port_candidate_id)
        shipped.append({**info, "at": b.updated_at.isoformat()})

    # project: confident-NEEDS burndown + the grab pool
    needs_ids = [iid for iid, fi in idx.items() if fi["verdict"] == "needs"]
    held = {b.port_candidate_id for b in items if b.assignee and not b.archived}
    needs_claimed = sum(1 for iid in needs_ids if iid in held)
    project = {"needs_total": len(needs_ids), "needs_claimed": needs_claimed,
               "unclaimed": len(needs_ids) - needs_claimed}

    stats = {
        "todo": len(active),
        "shipped": len(shipped_items),
        "available": project["unclaimed"],
        "self": sum(1 for b in active if via(b.port_candidate_id) == "self"),
        "assigned": sum(1 for b in active if via(b.port_candidate_id) == "assigned"),
    }

    my_events = sorted([e for e in events if e.actor == username],
                       key=lambda e: e.at, reverse=True)
    activity = []
    for e in my_events[:20]:
        info = await resolve(e.port_candidate_id)
        activity.append({"action": e.action, "at": e.at.isoformat(),
                         "to": e.to_value, "title": info["title"], "core": info["core"]})

    # last-14-day activity sparkline (events I authored)
    daycount = Counter(e.at.date().isoformat() for e in my_events)
    today = date.today()
    spark = [{"d": (today - timedelta(days=i)).isoformat(),
              "n": daycount.get((today - timedelta(days=i)).isoformat(), 0)}
             for i in range(13, -1, -1)]

    # small team overview: who holds / shipped what
    by_user: dict[str, dict] = defaultdict(lambda: {"todo": 0, "shipped": 0})
    for b in items:
        if not b.assignee:
            continue
        by_user[b.assignee]["shipped" if b.archived else "todo"] += 1
    team = [{"user": u, **c} for u, c in
            sorted(by_user.items(), key=lambda x: (-x[1]["todo"], -x[1]["shipped"]))]

    return {"me": username, "stats": stats, "queue": queue, "shipped": shipped,
            "activity": activity, "spark": spark, "team": team, "project": project}
