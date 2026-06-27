import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.config import settings as _settings
from mai.db.models import Commit, CommitFile, PortVerdict, ReviewAdvice
from mai.judge.ground import ground_opinion
from mai.judge.judge import choose_model
from mai.judge.prompt import PROMPT_VERSION
from mai.sync.verdicts import closeness_label

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.M)
_DOC = ("doc", "docs", "comment", "readme", "changelog")
_BUILD = ("cmake", "build", "ci", "makefile", ".yml", ".yaml")
_REFACTOR = ("refactor", "rename", "cleanup", "style", "format", "move")
_STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "fix", "fixes",
         "fixed", "add", "added", "update", "updated", "support", "by",
         "is", "it", "at", "as", "or", "no", "so", "me", "my", "if", "up",
         "be", "do", "we", "he", "its", "was", "are", "has", "had", "him",
         "his", "her", "but", "not", "all", "can"}


def _classify_type(title: str, paths: list[str]) -> str:
    t = title.lower()
    blob = t + " " + " ".join(paths).lower()
    if any(k in blob for k in _DOC):
        return "docs"
    if any(k in blob for k in _BUILD):
        return "build"
    if any(k in t for k in _REFACTOR):
        return "refactor"
    if any(k in t for k in ("crash", "bug", "leak", "wrong", "incorrect", "null", "deadlock")):
        return "bugfix"
    return "change"


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{2,}", s.lower()) if w not in _STOP}


def _rank_similar(rows: list[dict], title: str, *, limit: int = 3) -> list[dict]:
    want = _tokens(title)
    out = []
    for r in rows:
        have = _tokens(r["title"])
        inter = want & have
        score = round(len(inter) / len(want | have), 2) if (want | have) else 0.0
        if score > 0:
            out.append({**r, "score": score})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def _split_unified(patch: str) -> dict[str, list[str]]:
    """diff -> {path: [hunk_text, ...]} keyed by the b/ path of each file block."""
    files: dict[str, list[str]] = {}
    path, buf = None, []

    def flush():
        if path is not None and buf:
            files.setdefault(path, []).append("\n".join(buf))

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush(); buf = []; path = None
        elif line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("@@ "):
            flush(); buf = [line]
        elif buf:
            buf.append(line)
    flush()
    return files


def _hunk_old_start(hunk_text: str) -> int | None:
    m = _HUNK_RE.search(hunk_text)
    return int(m.group(1)) if m else None


def _hunk_header(hunk_text: str) -> str:
    return hunk_text.splitlines()[0] if hunk_text else ""


async def build_review_evidence(session: AsyncSession, git_client, item_id: str) -> dict | None:
    pg_id, _, core = item_id.rpartition(":")
    v = await session.scalar(select(PortVerdict).where(
        PortVerdict.patch_group_id == pg_id, PortVerdict.core == core))
    if v is None or v.verdict != "review":
        return None
    commit = await session.scalar(select(Commit).where(
        Commit.core == v.source_core, Commit.sha == v.source_sha))
    msg = (commit.message if commit else "") or ""
    title = msg.strip().splitlines()[0] if msg.strip() else item_id
    body = "\n".join(msg.strip().splitlines()[1:]).strip()
    files = list(await session.scalars(select(CommitFile).where(
        CommitFile.commit_id == (commit.id if commit else None))))
    paths = sorted({f.path for f in files})

    patch = await git_client.diff(v.source_core, v.source_sha)
    per_file = _split_unified(patch)
    rej_map = await git_client.rejected_hunks(core, patch, paths)
    rej_headers = {p: {_hunk_header(h) for h in _split_rej(rt)}
                   for p, rt in rej_map.items()}

    hunks, total, applied = [], 0, 0
    for p, hlist in per_file.items():
        for h in hlist:
            total += 1
            is_rej = _hunk_header(h) in rej_headers.get(p, set())
            tgt, tline = None, None
            if is_rej:
                tline = _hunk_old_start(h)
                if tline is not None:
                    tgt = await git_client.read_region(core, p, max(1, tline - 3), tline + 6) or None
            else:
                applied += 1
            hunks.append({"path": p, "applied": not is_rej, "patch_text": h,
                          "target_context": tgt, "target_line": tline})

    band = (closeness_label(v.conflict_applied, v.conflict_total)
            if v.conflict_total else None)
    similar = _rank_similar(await git_client.log_touching(core, paths), title)
    return {
        "item_id": item_id, "core": core,
        "fix": {"title": title, "body": body, "subsystem": v.subsystem,
                "source_core": v.source_core, "source_sha": v.source_sha,
                "magnitude": v.magnitude, "type": _classify_type(title, paths)},
        "conflict": {"applied": applied, "total": total, "band": band, "hunks": hunks},
        "similar": similar,
        "divergence": {"reason": ((v.evidence or [])[1:2] or [""])[0],
                       "relevance": v.relevance}}


def _split_rej(rej_text: str) -> list[str]:
    """Split a .rej file into individual hunk texts."""
    if not rej_text.strip():
        return []
    parts, cur = [], []
    for line in rej_text.splitlines():
        if line.startswith("@@ "):
            if cur:
                parts.append("\n".join(cur))
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        parts.append("\n".join(cur))
    return parts


def _opinion_from_row(row: ReviewAdvice) -> dict:
    return {"assessment": row.assessment, "confidence": row.confidence,
            "reason": row.reason, "tips": row.tips, "citations": row.citations,
            "adapted_hunks": row.adapted_hunks}


async def build_review_advice(session, git_client, judge, item_id, *, settings=_settings):
    """Collect evidence (P1); for a review item with a judge, return the cached grounded
    opinion on an exact-key hit, else compute -> ground -> upsert. Judge failures are not
    cached. Invariant 1: non-review -> evidence None, no judge, no cache."""
    evidence = await build_review_evidence(session, git_client, item_id)
    if evidence is None or judge is None:
        return {"evidence": evidence, "opinion": None}

    pg_id, _, core = item_id.rpartition(":")
    source_sha = (evidence.get("fix") or {}).get("source_sha")
    base_sha = await git_client.head_sha(core)
    model = choose_model(evidence, settings)

    row = await session.scalar(select(ReviewAdvice).where(
        ReviewAdvice.patch_group_id == pg_id, ReviewAdvice.core == core))
    if (row is not None and row.source_sha == source_sha and row.base_sha == base_sha
            and row.model == model and row.prompt_version == PROMPT_VERSION):
        return {"evidence": evidence, "opinion": _opinion_from_row(row)}   # cache hit

    try:
        opinion = ground_opinion(await judge.judge(evidence, model), evidence).model_dump()
    except Exception:  # noqa: BLE001 — a judge/network/schema failure must never 500
        return {"evidence": evidence, "opinion": None}   # do NOT cache failures

    if row is None:
        row = ReviewAdvice(patch_group_id=pg_id, core=core)
        session.add(row)
    row.source_sha = source_sha
    row.base_sha = base_sha
    row.model = model
    row.prompt_version = PROMPT_VERSION
    row.assessment = opinion["assessment"]
    row.confidence = opinion["confidence"]
    row.reason = opinion["reason"]
    row.tips = opinion["tips"]
    row.citations = opinion["citations"]
    row.adapted_hunks = opinion["adapted_hunks"]
    row.grounded = True
    await session.commit()
    return {"evidence": evidence, "opinion": opinion}
