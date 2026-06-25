"""Gate 3 — symbol precondition.

A patch can carry symbols its target lacks. `git apply` won't catch it when the
*removed* context is textually similar across forks (the #229 trap), so the apply
result is not enough. This gate asks, per touched file:

    Which identifiers does the patch's added code DEPEND ON (reference but not
    introduce), that resolve in the SOURCE's scope but are ABSENT from the TARGET's?

Any such identifier is a missing precondition: porting the change yields code that
references a symbol that does not exist there. The rule is conservative — an
identifier is flagged only when it provably resolves in the source (so it is a real
symbol, not noise) and provably does not in the target.
"""
from __future__ import annotations

from dataclasses import dataclass

from mai.cppindex import extract


@dataclass
class MissingSymbol:
    name: str
    detail: str          # human-readable: where it resolves in source, why it's absent


def _resolves(name: str, scope: set[str], file_syms: set[str]) -> bool:
    return name in scope or name in file_syms


def missing_in_file(
    *,
    source_bytes: bytes,
    target_bytes: bytes | None,
    added_lines: set[int],
    enclosing_name: str | None = None,
) -> list[MissingSymbol]:
    """Precondition symbols the target lacks for one touched C++ file.

    `source_bytes` is the file at the source commit (post-image); `target_bytes` is
    the file at the target HEAD, or None if it does not exist there.
    """
    if target_bytes is None:
        # No file to host the change at all -> every referenced symbol is moot; the
        # caller treats a missing file as file_absent. Return empty so the apply
        # router owns that case.
        return []

    referenced = extract.identifiers_on_lines(source_bytes, added_lines)
    introduced = extract.declared_on_lines(source_bytes, added_lines)
    free = referenced - introduced
    if not free:
        return []

    src_fn = (extract.find_function(source_bytes, enclosing_name) if enclosing_name
              else extract.function_covering(source_bytes, added_lines))
    src_scope = src_fn.scope_names if src_fn else set()
    src_syms = extract.file_symbols(source_bytes)

    # Only consider identifiers that genuinely resolve in the source — this filters
    # out method names / externals the source TU doesn't define either (we can't
    # prove those are preconditions, so we don't flag them).
    candidates = {n for n in free if _resolves(n, src_scope, src_syms)}
    if not candidates:
        return []

    tgt_fn = (extract.find_function(target_bytes, src_fn.name) if src_fn
              else (extract.find_function(target_bytes, enclosing_name)
                    if enclosing_name else None))
    tgt_scope = tgt_fn.scope_names if tgt_fn else set()
    tgt_syms = extract.file_symbols(target_bytes)

    missing: list[MissingSymbol] = []
    for name in sorted(candidates):
        if _resolves(name, tgt_scope, tgt_syms):
            continue
        where = _origin(name, src_fn, src_scope, src_syms)
        if src_fn and tgt_fn is None:
            detail = (f"'{name}' ({where}); target has no function "
                      f"'{src_fn.name}' to host it")
        else:
            detail = f"'{name}' ({where}) is absent in target"
        missing.append(MissingSymbol(name=name, detail=detail))
    return missing


def _origin(name: str, src_fn, src_scope: set[str], src_syms: set[str]) -> str:
    if src_fn and name in src_fn.params:
        return f"parameter of {src_fn.name}"
    if name in src_scope:
        return f"local in {src_fn.name}" if src_fn else "local"
    if name in src_syms:
        return "file-level symbol in source"
    return "referenced in source"
