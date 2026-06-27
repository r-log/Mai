"""Task-scoped C++ symbol extraction for the portability gates.

NOT a general indexer. It answers exactly the questions Gate 3 asks of a single
translation unit: what functions are defined here, what does each take as
parameters / declare as locals, and what identifiers does this file define at all.
Everything is derived from a Tree-sitter parse (see parser.py) so it survives
multiline signatures, macros, and comments that a regex would mis-read.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from mai.cppindex.parser import parse_root

# Bump whenever the EXTRACTED OUTPUT changes (grammar, new symbol kinds, params/locals
# logic). It is a code-memory cache validity key — stale rows are re-indexed on a bump.
EXTRACT_VERSION = 1

# Declarator wrappers to descend through to reach the named identifier of a
# function or parameter (e.g. `char* foo()` -> pointer_declarator -> function_declarator).
_DECL_WRAPPERS = {
    "pointer_declarator", "reference_declarator", "parenthesized_declarator",
    "array_declarator",
}


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _descend_declarator(node: Node, target_types: set[str]) -> Node | None:
    """Walk the `declarator` field through wrappers until a target node is hit."""
    cur: Node | None = node
    seen = 0
    while cur is not None and seen < 12:
        if cur.type in target_types:
            return cur
        if cur.type in _DECL_WRAPPERS or cur.type == "init_declarator":
            cur = cur.child_by_field_name("declarator")
            seen += 1
            continue
        return None
    return None


def _declared_name(declarator: Node | None, src: bytes) -> str | None:
    """The bare identifier a declarator names (descending pointer/array/ref wrappers)."""
    ident = _descend_declarator(
        declarator, {"identifier", "field_identifier", "qualified_identifier"}
    ) if declarator else None
    if ident is None:
        return None
    if ident.type == "qualified_identifier":
        name = ident.child_by_field_name("name")
        return _text(name, src) if name is not None else _text(ident, src)
    return _text(ident, src)


@dataclass
class CppFunction:
    name: str                       # unqualified (e.g. AutoProduceStrings)
    qualified_name: str             # as written (e.g. DBCFileLoader::AutoProduceStrings)
    start_line: int                 # 1-based, inclusive
    end_line: int                   # 1-based, inclusive
    params: list[str] = field(default_factory=list)   # named params, in order
    locals: set[str] = field(default_factory=set)     # names declared in the body

    @property
    def scope_names(self) -> set[str]:
        """Identifiers that resolve inside this function: parameters + locals."""
        return set(self.params) | self.locals

    def covers(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


def _function_declarator(fn_def: Node) -> Node | None:
    return _descend_declarator(
        fn_def.child_by_field_name("declarator"), {"function_declarator"})


def _param_names(fn_decl: Node, src: bytes) -> list[str]:
    params = fn_decl.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for child in params.children:
        if child.type != "parameter_declaration":
            continue
        name = _declared_name(child.child_by_field_name("declarator"), src)
        if name:
            out.append(name)
    return out


def _local_names(body: Node, src: bytes) -> set[str]:
    """Names declared anywhere within a function body (declarations, for-inits, etc.)."""
    out: set[str] = set()
    stack = list(body.children)
    while stack:
        n = stack.pop()
        if n.type == "declaration":
            for c in n.children:
                if c.type == "init_declarator":
                    name = _declared_name(c, src)
                elif c.type in _DECL_WRAPPERS or c.type == "identifier":
                    name = _declared_name(c, src)
                else:
                    name = None
                if name:
                    out.add(name)
        stack.extend(n.children)
    return out


def _build_function(fn_def: Node, src: bytes) -> CppFunction | None:
    fn_decl = _function_declarator(fn_def)
    if fn_decl is None:
        return None
    name_node = fn_decl.child_by_field_name("declarator")
    qualified = _text(name_node, src) if name_node is not None else ""
    name = _declared_name(name_node, src) or qualified
    if not name:
        return None
    body = fn_def.child_by_field_name("body")
    return CppFunction(
        name=name,
        qualified_name=qualified,
        start_line=fn_def.start_point[0] + 1,
        end_line=fn_def.end_point[0] + 1,
        params=_param_names(fn_decl, src),
        locals=_local_names(body, src) if body is not None else set(),
    )


def functions(source: bytes) -> list[CppFunction]:
    """Every function *definition* in the translation unit (free + member)."""
    root = parse_root(source)
    out: list[CppFunction] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "function_definition":
            fn = _build_function(n, source)
            if fn is not None:
                out.append(fn)
        stack.extend(n.children)
    return out


def function_covering(source: bytes, lines: set[int]) -> CppFunction | None:
    """The innermost function whose range covers the most of `lines` (the enclosing fn)."""
    best: CppFunction | None = None
    best_hits = 0
    for fn in functions(source):
        hits = sum(1 for ln in lines if fn.covers(ln))
        if hits > best_hits:
            best, best_hits = fn, hits
    return best


def find_function(source: bytes, name: str) -> CppFunction | None:
    """The first function definition with the given unqualified name."""
    for fn in functions(source):
        if fn.name == name:
            return fn
    return None


# Node types whose declared identifier is a file-level symbol definition.
_SYMBOL_DECL_TYPES = {
    "function_definition", "class_specifier", "struct_specifier",
    "union_specifier", "enum_specifier", "type_definition", "alias_declaration",
    "preproc_def", "preproc_function_def", "enumerator", "field_declaration",
    "declaration",
}


def identifiers_on_lines(source: bytes, lines: set[int]) -> set[str]:
    """Bare `identifier` tokens appearing on the given (1-based) lines.

    Only `identifier` nodes — keywords, primitive types, `field_identifier` (a.b
    member access), comments and string literals are distinct node types and are
    naturally excluded. That is exactly "names the code references".
    """
    if not lines:
        return set()
    root = parse_root(source)
    out: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "identifier" and (n.start_point[0] + 1) in lines:
            out.add(_text(n, source))
        stack.extend(n.children)
    return out


def declared_on_lines(source: bytes, lines: set[int]) -> set[str]:
    """Names *introduced* by the patch — declared/defined on the given lines.

    These are symbols the patch brings with it (a new local, param, function, type or
    macro) — NOT preconditions the target must already satisfy. Covers both in-function
    declarations and new file-scope definitions, so a patch that adds and uses its own
    helper is never flagged as depending on a missing symbol.
    """
    if not lines:
        return set()
    root = parse_root(source)
    out: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "declaration":
            for c in n.children:
                nm = _declared_name(c, source) if c.type in (
                    "init_declarator", "identifier", *_DECL_WRAPPERS) else None
                if nm and _on_lines(c, lines):
                    out.add(nm)
        elif n.type in ("init_declarator", "parameter_declaration"):
            nm = _declared_name(n.child_by_field_name("declarator"), source)
            if nm and _on_lines(n, lines):
                out.add(nm)
        elif n.type == "function_definition":
            fn_decl = _function_declarator(n)
            name_node = fn_decl.child_by_field_name("declarator") if fn_decl else None
            nm = _declared_name(name_node, source)
            if nm and name_node is not None and _on_lines(name_node, lines):
                out.add(nm)
        elif n.type in ("preproc_def", "preproc_function_def", "enumerator",
                        "class_specifier", "struct_specifier", "union_specifier",
                        "enum_specifier", "type_definition", "alias_declaration"):
            name_node = n.child_by_field_name("name")
            if name_node is not None and _on_lines(name_node, lines):
                out.add(_text(name_node, source))
        stack.extend(n.children)
    return out


def _on_lines(node: Node, lines: set[int]) -> bool:
    return (node.start_point[0] + 1) in lines


def file_symbols(source: bytes) -> set[str]:
    """Identifiers this translation unit *defines* (functions, types, macros, members…).

    Deliberately over-inclusive: a symbol wrongly counted present only makes Gate 3
    MORE conservative (fewer NOT_APPLICABLE), which is the safe direction.
    """
    root = parse_root(source)
    out: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in _SYMBOL_DECL_TYPES:
            _collect_symbol_names(n, source, out)
        stack.extend(n.children)
    return out


def _collect_symbol_names(n: Node, src: bytes, out: set[str]) -> None:
    t = n.type
    if t in ("preproc_def", "preproc_function_def"):
        name = n.child_by_field_name("name")
        if name is not None:
            out.add(_text(name, src))
        return
    if t == "enumerator":
        name = n.child_by_field_name("name")
        if name is not None:
            out.add(_text(name, src))
        return
    if t in ("class_specifier", "struct_specifier", "union_specifier", "enum_specifier"):
        name = n.child_by_field_name("name")
        if name is not None:
            out.add(_text(name, src))
        return
    if t == "function_definition":
        fn_decl = _function_declarator(n)
        if fn_decl is not None:
            nm = _declared_name(fn_decl.child_by_field_name("declarator"), src)
            if nm:
                out.add(nm)
        return
    # declaration / field_declaration / type_definition / alias_declaration:
    # collect each declarator's bare name.
    for c in n.children:
        if c.type in _DECL_WRAPPERS or c.type in (
                "init_declarator", "function_declarator", "identifier",
                "field_identifier", "type_identifier", "qualified_identifier"):
            if c.type == "function_declarator":
                nm = _declared_name(c.child_by_field_name("declarator"), src)
            else:
                nm = _declared_name(c, src)
            if nm:
                out.add(nm)
