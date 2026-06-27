from dataclasses import dataclass, field

from sqlalchemy import select

from mai.cppindex import extract
from mai.cppindex.extract import CppFunction
from mai.db.models import CodeFileIndex


@dataclass
class FileIndex:
    exists: bool
    file_symbols: set[str] = field(default_factory=set)
    functions: list[CppFunction] = field(default_factory=list)

    def find_function(self, name: str) -> CppFunction | None:
        for fn in self.functions:        # tree order — mirrors extract.find_function
            if fn.name == name:
                return fn
        return None


def _fn_to_dict(fn: CppFunction) -> dict:
    return {"name": fn.name, "qualified_name": fn.qualified_name,
            "start_line": fn.start_line, "end_line": fn.end_line,
            "params": list(fn.params), "locals": sorted(fn.locals)}


def _fn_from_dict(d: dict) -> CppFunction:
    return CppFunction(name=d["name"], qualified_name=d["qualified_name"],
                       start_line=d["start_line"], end_line=d["end_line"],
                       params=list(d["params"]), locals=set(d["locals"]))


async def get_file_index(session, git_client, core: str, base_sha: str, path: str) -> FileIndex:
    """Cached tree-sitter extraction for (core, base_sha, path). Cache hit -> reconstruct;
    miss -> read+parse and add a row (NO commit — the caller's transaction owns it; autoflush
    makes a within-run repeat lookup a hit). session=None or any cache fault -> direct parse,
    no persistence. Never raises for cache reasons."""
    # NOTE: when `session` is provided it MUST be autoflush=True (the default, and what
    # SessionFactory uses): a within-run repeat lookup relies on read-your-own-writes so a
    # file is parsed once per (core, base_sha, path), never double-added.
    cache_ok = session is not None
    row = None
    if cache_ok:
        try:
            row = await session.scalar(select(CodeFileIndex).where(
                CodeFileIndex.core == core, CodeFileIndex.base_sha == base_sha,
                CodeFileIndex.path == path))
        except Exception:  # noqa: BLE001 — missing table / cache fault -> parse directly
            row, cache_ok = None, False
        else:
            if row is not None and row.index_version == extract.EXTRACT_VERSION:
                return FileIndex(
                    exists=row.exists, file_symbols=set(row.file_symbols),
                    functions=[_fn_from_dict(d) for d in row.functions])

    text = await git_client.read_file(core, base_sha, path)
    if text is None:
        idx = FileIndex(exists=False)
        fns_json, syms_json = [], []
    else:
        b = text.encode("utf-8", "replace")
        funcs = extract.functions(b)
        syms = extract.file_symbols(b)
        idx = FileIndex(exists=True, file_symbols=syms, functions=funcs)
        fns_json = [_fn_to_dict(f) for f in funcs]
        syms_json = sorted(syms)

    if cache_ok:
        # add a new row, or overwrite a stale-version one in place (uq is (core,base_sha,path)).
        # No commit/flush — the caller's transaction owns the commit.
        if row is None:
            session.add(CodeFileIndex(
                core=core, base_sha=base_sha, path=path, exists=idx.exists,
                file_symbols=syms_json, functions=fns_json,
                index_version=extract.EXTRACT_VERSION))
        else:
            row.exists = idx.exists
            row.file_symbols = syms_json
            row.functions = fns_json
            row.index_version = extract.EXTRACT_VERSION
    return idx
