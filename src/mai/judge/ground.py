# src/mai/judge/ground.py
from mai.judge.schema import ReviewOpinion


def _evidence_tokens(evidence: dict) -> set[str]:
    toks: set[str] = set()
    sha = ((evidence.get("fix") or {}).get("source_sha"))
    if sha:
        toks.add(sha)
        toks.add(sha[:10])
    for h in (evidence.get("conflict") or {}).get("hunks") or []:
        path = h.get("path")
        if path:
            toks.add(path)
            tline = h.get("target_line")
            if tline is not None:
                toks.add(f"{path}:{tline}")
    for s in evidence.get("similar") or []:
        ssha = s.get("sha")
        if ssha:
            toks.add(ssha)
            toks.add(ssha[:10])
    return toks


def _cites(text: str, tokens: set[str]) -> bool:
    return any(tok and tok in text for tok in tokens)


def ground_opinion(opinion: ReviewOpinion, evidence: dict) -> ReviewOpinion:  # note: assessment+reason are labeled opinion, not grounded
    """Drop every claim not grounded in the evidence; discount confidence by the
    grounded fraction. All-ungrounded -> uncertain/0 with a manual-review note."""
    tokens = _evidence_tokens(evidence)
    kept_tips = [t for t in opinion.tips if _cites(t, tokens)]
    kept_cites = [c for c in opinion.citations if _cites(c, tokens)]
    kept_hunks = [h for h in opinion.adapted_hunks if h.path in tokens]
    total = len(opinion.tips) + len(opinion.citations) + len(opinion.adapted_hunks)
    kept = len(kept_tips) + len(kept_cites) + len(kept_hunks)

    if total > 0 and kept == 0:
        return opinion.model_copy(update={
            "assessment": "uncertain",
            "confidence": 0.0,
            "reason": opinion.reason + " [model output ungrounded — manual review]",
            "tips": [], "citations": [], "adapted_hunks": [],
        })

    fraction = 1.0 if total == 0 else kept / total
    return opinion.model_copy(update={
        "confidence": round(opinion.confidence * fraction, 3),
        "tips": kept_tips, "citations": kept_cites, "adapted_hunks": kept_hunks,
    })
