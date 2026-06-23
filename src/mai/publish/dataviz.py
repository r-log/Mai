import json
import math
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import (Commit, DriftObservation, PatchGroup, PortCandidate,
                           PortVerdict, Report, Repo, SourceRecord, Verification)
from mai.sync.verdicts import closeness_label
from mai.publish.areas import AREAS, area_of
from mai.publish.slug import safe_slug
from mai.publish.views import counts, iter_bug_reports, report_bundle
from mai.repository.reports import ReportRepository

_AREA_COLOR = {a["name"]: a["color"] for a in AREAS}
_STOPS = [(0x2e, 0xa0, 0x43), (0xd2, 0x99, 0x22), (0xf8, 0x51, 0x49)]  # green, amber, red


def _short_core(full_name: str) -> str:
    org = full_name.split("/")[0]
    return (org[len("mangos"):] if org.startswith("mangos") else org).title() or full_name


def heat_hex(pct: float) -> str:
    """Map a divergence percentage (~55..90) to a green->amber->red hex color."""
    t = max(0.0, min(1.0, (pct - 55) / 35.0))
    lo, hi, u = (_STOPS[0], _STOPS[1], t * 2) if t < 0.5 else (_STOPS[1], _STOPS[2], (t - 0.5) * 2)
    rgb = tuple(round(lo[i] + (hi[i] - lo[i]) * u) for i in range(3))
    return "#%02x%02x%02x" % rgb


async def build_drift_matrix(session: AsyncSession) -> dict:
    agg: dict[tuple[str, str], dict] = {}
    for o in await session.scalars(select(DriftObservation)):
        key = tuple(sorted((o.fork_a, o.fork_b)))
        bucket = agg.setdefault(key, {"shared": 0, "diverged": 0})
        bucket["shared"] += o.shared
        bucket["diverged"] += o.diverged
    cores = sorted({c for key in agg for c in key})
    rows = []
    for a in cores:
        cells = []
        for b in cores:
            if a == b:
                cells.append({"self": True})
                continue
            bucket = agg.get(tuple(sorted((a, b))))
            if bucket and bucket["shared"]:
                pct = round(100 * bucket["diverged"] / bucket["shared"])
                cells.append({"value": pct, "color": heat_hex(pct)})
            else:
                cells.append({"value": None})
        rows.append({"core": _short_core(a), "full": a, "cells": cells})
    return {"cores": [_short_core(c) for c in cores], "rows": rows}


async def build_dashboard(session: AsyncSession) -> dict:
    stats = await counts(session)
    area_counts: dict[str, int] = {}
    for report in await iter_bug_reports(session):
        bundle = await report_bundle(session, report)
        area_counts[bundle.area] = area_counts.get(bundle.area, 0) + 1
    top_areas = [{"name": name, "count": n, "color": _AREA_COLOR.get(name, "#59636e")}
                 for name, n in sorted(area_counts.items(), key=lambda kv: kv[1], reverse=True)]
    fixed = []
    rr = ReportRepository(session)
    vs = await session.scalars(
        select(Verification).where(Verification.verdict == "fixed_confirmed").limit(10))
    for v in vs:
        rep = await rr.get_by_id(v.report_id)
        if rep is None:
            continue
        related = (v.evidence[0].get("related", "")
                   if v.evidence and isinstance(v.evidence[0], dict) else "")
        fixed.append({"id": rep.canonical_key, "title": rep.title, "core": rep.core,
                      "related": related, "url": f"/{rep.core}/bugs/{safe_slug(rep.canonical_key)}/"})
    per_core_rows = await session.execute(
        select(Report.core, func.count(Report.id)).group_by(Report.core))
    per_core = sorted(
        ({"core": core, "reports": n} for core, n in per_core_rows),
        key=lambda c: c["reports"], reverse=True)
    coverage = {
        "total": stats["reports"],
        "enriched": stats["enriched"],
        "cores": per_core,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {"stats": stats, "top_areas": top_areas,
            "recently_fixed": fixed, "coverage": coverage}


async def build_frequency(session: AsyncSession, top_n: int = 6) -> dict:
    """Per-core, per-subsystem divergence intensity as a stacked heightfield."""
    obs = list(await session.scalars(select(DriftObservation)))
    if not obs:
        return {"cores": [], "subsystems": [], "intensity": {}, "max": 1.6}
    forks = sorted({o.fork_a for o in obs} | {o.fork_b for o in obs})

    shared_by_sub: dict[str, int] = {}
    for o in obs:
        shared_by_sub[o.subsystem] = shared_by_sub.get(o.subsystem, 0) + o.shared
    top = [s for s, _ in sorted(shared_by_sub.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]

    subsystems = []
    for i, full in enumerate(top):
        ang = 2 * math.pi * i / max(1, len(top))
        subsystems.append({"name": full.split("/")[-1], "full": full,
                           "x": round(4.5 * math.cos(ang), 2), "z": round(4.5 * math.sin(ang), 2)})

    intensity: dict[str, dict] = {}
    for fork in forks:
        per_sub = {}
        for sub in subsystems:
            vals = [o.diverged / o.shared for o in obs
                    if o.subsystem == sub["full"] and o.shared
                    and fork in (o.fork_a, o.fork_b)]
            per_sub[sub["full"]] = round(sum(vals) / len(vals), 3) if vals else None
        intensity[fork] = per_sub

    spacing, n = 2.4, len(forks)
    cores = [{"name": _short_core(f), "full": f,
              "y": round((n - 1) / 2 * spacing - i * spacing, 2)}
             for i, f in enumerate(forks)]
    return {"cores": cores, "subsystems": subsystems, "intensity": intensity, "max": 1.6}


async def _latest_payload(session: AsyncSession, source_type: str, source_id: str) -> dict:
    rec = await session.scalar(
        select(SourceRecord)
        .where(SourceRecord.source_type == source_type, SourceRecord.source_id == source_id)
        .order_by(SourceRecord.version.desc()).limit(1))
    return rec.payload if rec else {}


async def build_pushes(session: AsyncSession, limit: int = 8) -> dict:
    """Recent merged PRs grouped by core, for the porting board's 'what landed' columns."""
    rows = await session.scalars(
        select(Report).where(Report.canonical_key.like("gh_pr:%"),
                             Report.status == "merged"))
    by_core: dict[str, list] = {}
    for r in rows:
        source_id = r.canonical_key[len("gh_pr:"):]
        payload = await _latest_payload(session, "gh_pr", source_id)
        merged_at = payload.get("merged_at") or ""
        repo = source_id.split("#")[0]
        by_core.setdefault(r.core, []).append({
            "title": r.title,
            "area": area_of(r.title, None, payload),
            "pr": payload.get("number"),
            "url": payload.get("html_url", ""),
            "repo": repo,
            "merged_at": merged_at,
        })
    cores = []
    for core, pushes in sorted(by_core.items()):
        pushes.sort(key=lambda p: p["merged_at"], reverse=True)
        repo = pushes[0]["repo"] if pushes else ""
        cores.append({"core": core, "repo": repo, "pushes": pushes[:limit]})
    return {"cores": cores}


_TIER_RANK = {"surgical": 0, "small": 1, "moderate": 2, "bulk": 3}
_CORE_ORDER = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4}


async def _source_repos(session: AsyncSession) -> dict[str, str]:
    """core -> repo full_name for building commit URLs (prefer the .../server repo)."""
    repos: dict[str, str] = {}
    for r in await session.scalars(select(Repo)):
        if r.core not in repos or r.full_name.endswith("/server"):
            repos[r.core] = r.full_name
    return repos


async def build_port_candidates(session: AsyncSession) -> dict:
    """Open port candidates grouped by target fork, quick-wins first, for /port/."""
    repos = await _source_repos(session)
    pg_patch = {pg.id: pg.patch_id for pg in await session.scalars(select(PatchGroup))}
    cands = list(await session.scalars(
        select(PortCandidate).where(PortCandidate.status == "open")))

    tiers = {"surgical": 0, "small": 0, "moderate": 0, "bulk": 0}
    by_target: dict[str, list] = {}
    for pc in cands:
        commit = await session.scalar(
            select(Commit).where(Commit.core == pc.source_core,
                                 Commit.sha == pc.source_sha))
        title = commit.message.strip().splitlines()[0] if commit and commit.message else ""
        if not title:
            title = f"{pc.subsystem} fix ({(pc.source_sha or '')[:8]})"
        repo = repos.get(pc.source_core)
        source_url = (f"https://github.com/{repo}/commit/{pc.source_sha}"
                      if repo and pc.source_sha else None)
        by_target.setdefault(pc.target_core, []).append({
            "id": f"{pc.patch_group_id}:{pc.target_core}",
            "title": title,
            "source_core": pc.source_core,
            "source_url": source_url,
            "subsystem": pc.subsystem,
            "tier": pc.tier,
            "magnitude": pc.magnitude,
            "confidence": pc.confidence,
            "patch_id": (pg_patch.get(pc.patch_group_id) or "")[:12],
            "evidence": pc.evidence,
        })
        if pc.tier in tiers:
            tiers[pc.tier] += 1

    all_targets = sorted(set(by_target) | set(_CORE_ORDER),
                         key=lambda c: (_CORE_ORDER.get(c, 99), c))
    columns = []
    for core in all_targets:
        items = by_target.get(core, [])
        items.sort(key=lambda x: (_TIER_RANK.get(x["tier"], 9), x["magnitude"]))
        columns.append({"core": core, "repo": repos.get(core, ""),
                        "count": len(items), "candidates": items})
    return {"summary": {"total": len(cands), "tiers": tiers}, "columns": columns}


def _review_reason(v: "PortVerdict") -> str:
    if v.apply_result == "conflict" and v.conflict_total:
        b = closeness_label(v.conflict_applied, v.conflict_total)
        return f"conflict — {v.conflict_applied}/{v.conflict_total} hunks ({b})"
    if v.apply_result == "conflict":
        return "conflict — binary/blob change"
    return "diverged — needs adaptation"


def _band_rank(entry: dict) -> int:
    return {"near": 0, "partial": 1, "far": 2}.get(entry.get("band"), 3)


async def build_port_verdicts(session: AsyncSession) -> dict:
    """Per-fix cross-core port matrix for /port/, read straight off PortVerdict.

    One card per fix that has >=1 needs|review core; each card lists which cores
    need it (claimable), should be reviewed (claimable, with closeness), already
    have it, or can't use it. REVIEW is ranked near->partial->far. Truthful by
    construction: it never re-grades a verdict, only groups them.
    """
    repos = await _source_repos(session)
    rows = list(await session.scalars(select(PortVerdict)))
    by_fix: dict[str, list] = {}
    for v in rows:
        by_fix.setdefault(v.patch_group_id, []).append(v)
    cores = sorted({v.core for v in rows} | set(_CORE_ORDER),
                   key=lambda c: (_CORE_ORDER.get(c, 99), c))

    summary: dict[str, int] = {"needs": 0, "review": 0, "na": 0, "has_it": 0}
    fixes: list[dict] = []
    for pg_id, vs in by_fix.items():
        rep = vs[0]   # source_core/sha/subsystem/tier/magnitude identical across the group
        commit = await session.scalar(
            select(Commit).where(Commit.core == rep.source_core,
                                 Commit.sha == rep.source_sha))
        title = commit.message.strip().splitlines()[0] if commit and commit.message else ""
        if not title:
            title = f"{rep.subsystem} fix ({(rep.source_sha or '')[:8]})"
        repo = repos.get(rep.source_core)
        source_url = (f"https://github.com/{repo}/commit/{rep.source_sha}"
                      if repo and rep.source_sha else None)

        needs, review, na, has_it = [], [], [], []
        for v in sorted(vs, key=lambda v: (_CORE_ORDER.get(v.core, 99), v.core)):
            item_id = f"{pg_id}:{v.core}"
            if v.verdict == "needs":
                needs.append({"core": v.core, "item_id": item_id})
            elif v.verdict == "review":
                entry: dict = {"core": v.core, "item_id": item_id,
                               "reason": _review_reason(v)}
                if v.conflict_total:
                    entry["applied"] = v.conflict_applied
                    entry["total"] = v.conflict_total
                    entry["band"] = closeness_label(v.conflict_applied, v.conflict_total)
                review.append(entry)
            elif v.verdict == "has_it":
                has_it.append({"core": v.core})
            else:
                na.append({"core": v.core, "reason": "code not present"})
        if not needs and not review:
            continue   # not actionable -> no card

        review.sort(key=lambda e: (_band_rank(e),
                                   -(e.get("applied", 0) / e.get("total", 1))))
        summary["needs"] += len(needs)
        summary["review"] += len(review)
        summary["na"] += len(na)
        summary["has_it"] += len(has_it)
        fixes.append({
            "id": pg_id, "title": title, "source_core": rep.source_core,
            "source_url": source_url, "subsystem": rep.subsystem,
            "tier": rep.tier, "magnitude": rep.magnitude,
            "needs": needs, "review": review, "na": na, "has_it": has_it})

    def _best_band(f: dict) -> int:
        return min((_band_rank(e) for e in f["review"]), default=3)
    fixes.sort(key=lambda f: (0 if f["needs"] else 1, _best_band(f),
                              _TIER_RANK.get(f["tier"], 9), f["magnitude"]))
    summary["fixes"] = len(fixes)
    return {"summary": summary, "cores": cores, "fixes": fixes}


async def write_dataviz(session: AsyncSession, out_dir: str) -> None:
    data = Path(out_dir) / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "drift.json").write_text(
        json.dumps(await build_drift_matrix(session), indent=2), encoding="utf-8")
    (data / "dashboard.json").write_text(
        json.dumps(await build_dashboard(session), indent=2), encoding="utf-8")
    (data / "frequency.json").write_text(
        json.dumps(await build_frequency(session), indent=2), encoding="utf-8")
    (data / "pushes.json").write_text(
        json.dumps(await build_pushes(session), indent=2), encoding="utf-8")
    (data / "port_candidates.json").write_text(
        json.dumps(await build_port_candidates(session), indent=2), encoding="utf-8")
