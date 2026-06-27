"""The portability classifier — a gate funnel over one (commit, target_core) pair.

    evaluate(...) -> Verdict{state, confidence, evidence}

Gates run cheapest-first and short-circuit on the first Terminal. Deterministic given
(source_sha, target_HEAD_sha, GATE_SUITE_VERSION): every input is read from git at
those refs, no clocks, no network beyond the local mirrors.

Gate 1 — Equivalence       : patch reverse-applies -> ALREADY_PRESENT
Gate 2 — Mechanical apply   : clean | conflict | file_absent (a ROUTER, not a verdict)
Gate 3 — Symbol precondition: a depended-on symbol absent in target -> NOT_APPLICABLE;
                              else clean->PORTABLE, conflict->ADAPTABLE

Out of scope (Phase 1) — left as seams, not built:
  # TODO(necessity): does the bug even manifest in target (root-cause join)
  # TODO(policy): durable maintainer divergence facts ("Zero stays pre-multilocale")
  # TODO(compile-probe): apply + scoped build of the touched TU
  # TODO(uncertain-llm): LLM adjudication of UNCERTAIN
  # TODO(rename-map): follow renames/moves for the target path (uses old_path today)
  # TODO(hunk-split): evaluate per-hunk for non-atomic commits
"""
from __future__ import annotations

from mai.codememory.index import FileIndex
from mai.cppindex import extract
from mai.portability.patch import parse_patch
from mai.portability.symbols import missing_in_file
from mai.portability.types import Evidence, GATE_SUITE_VERSION, State, Verdict

_CPP_SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx", ".inl")


def _is_cpp(path: str) -> bool:
    return path.lower().endswith(_CPP_SUFFIXES)


async def evaluate(
    git_client,
    *,
    source_core: str,
    source_sha: str,
    target_core: str,
    target_head: str | None = None,
) -> Verdict:
    if target_head is None:
        target_head = await git_client.head_sha(target_core)
    patch = await git_client.diff(source_core, source_sha)

    # Gate 1 — Equivalence: the fix already reverse-applies => it is already present.
    if await git_client.apply_check(target_core, patch, reverse=True) == "reverse_clean":
        return Verdict(State.ALREADY_PRESENT, "high", [Evidence(
            "equivalence", "reverse_clean",
            f"patch reverse-applies cleanly to {target_core}@{target_head[:8]}")])

    # Gate 2 — Mechanical apply: route on the apply outcome (not yet a verdict).
    apply_result = await git_client.apply_check(target_core, patch)
    return await classify_from_apply(
        git_client, patch=patch, apply_result=apply_result,
        source_core=source_core, source_sha=source_sha,
        target_core=target_core, target_head=target_head)


async def classify_from_apply(
    git_client,
    *,
    patch: str,
    apply_result: str,
    source_core: str,
    source_sha: str,
    target_core: str,
    target_head: str,
) -> Verdict:
    """Gates 2→3 given an already-computed apply outcome (`reverse_clean`, `clean`,
    `conflict`, or `file_absent`). Shared by `evaluate` and by `compute_verdicts`, so
    the live pipeline reuses its apply result instead of re-running git."""
    if apply_result == "reverse_clean":
        return Verdict(State.ALREADY_PRESENT, "high", [Evidence(
            "equivalence", "reverse_clean", "patch reverse-applies cleanly")])

    evidence = [Evidence("mechanical_apply", apply_result,
                         f"git apply --check -> {apply_result}")]
    if apply_result == "file_absent":
        evidence.append(Evidence("symbol_precondition", "file_absent",
                                 "a touched file does not exist in target"))
        return Verdict(State.NOT_APPLICABLE, "high", evidence)

    # Gate 3 — Symbol precondition: read source post-image + target HEAD per touched
    # C++ file; flag depended-on symbols that resolve in source but not target.
    missing: list[str] = []
    checked_cpp = False
    for fp in parse_patch(patch):
        path = fp.new_path
        if path is None or fp.is_delete or not _is_cpp(path) or not fp.added_lines:
            continue
        checked_cpp = True
        source_text = await git_client.read_file(source_core, source_sha, path)
        if source_text is None:
            continue
        target_text = await git_client.read_file(target_core, target_head, path)
        if target_text is not None:
            tgt_b = target_text.encode("utf-8", "replace")
            target_index = FileIndex(
                exists=True,
                file_symbols=extract.file_symbols(tgt_b),
                functions=extract.functions(tgt_b),
            )
        else:
            target_index = FileIndex(exists=False)
        for ms in missing_in_file(
                source_bytes=source_text.encode("utf-8", "replace"),
                target_index=target_index,
                added_lines=fp.added_lines):
            missing.append(f"{path}: required symbol {ms.detail}")

    if missing:
        for detail in missing:
            evidence.append(Evidence("symbol_precondition", "missing_symbol", detail))
        return Verdict(State.NOT_APPLICABLE, "high", evidence)

    evidence.append(Evidence(
        "symbol_precondition", "ok",
        "symbols present" if checked_cpp
        else "no C++ files to symbol-check (apply outcome only)"))
    if apply_result == "clean":
        return Verdict(State.PORTABLE, "high" if checked_cpp else "medium", evidence)
    # conflict / no-apply but no missing symbol => relevant, needs a hand-port.
    return Verdict(State.ADAPTABLE, "medium", evidence)


def cache_key(source_sha: str, target_head: str) -> tuple[str, str, str]:
    """The tuple a verdict is pure over; recompute when any element changes."""
    return (source_sha, target_head, GATE_SUITE_VERSION)
