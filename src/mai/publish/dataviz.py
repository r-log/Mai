import json
import math
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import DriftObservation, Report, Verification
from mai.publish.areas import AREAS
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
        select(Report.core, func.count()).group_by(Report.core))
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
            if vals:
                per_sub[sub["full"]] = round(sum(vals) / len(vals) * 1.5, 3)
        intensity[fork] = per_sub

    spacing, n = 2.4, len(forks)
    cores = [{"name": _short_core(f), "full": f,
              "y": round((n - 1) / 2 * spacing - i * spacing, 2)}
             for i, f in enumerate(forks)]
    return {"cores": cores, "subsystems": subsystems, "intensity": intensity, "max": 1.6}


async def write_dataviz(session: AsyncSession, out_dir: str) -> None:
    data = Path(out_dir) / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "drift.json").write_text(
        json.dumps(await build_drift_matrix(session), indent=2), encoding="utf-8")
    (data / "dashboard.json").write_text(
        json.dumps(await build_dashboard(session), indent=2), encoding="utf-8")
    (data / "frequency.json").write_text(
        json.dumps(await build_frequency(session), indent=2), encoding="utf-8")
