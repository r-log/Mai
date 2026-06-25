"""Tree-sitter C++ parser, cached.

Adopts GITA's Language -> Parser pattern (src/gita/indexer/ts_loader.py) but is
mai-owned and C++-only: mangos is C++, which GITA does not cover. One Language and
one Parser are built lazily and reused — Tree-sitter parsers are cheap to reuse and
expensive to construct.
"""
from __future__ import annotations

from tree_sitter import Node, Parser, Tree

_PARSER: Parser | None = None


def _parser() -> Parser:
    global _PARSER
    if _PARSER is None:
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language

        _PARSER = Parser(Language(tscpp.language()))
    return _PARSER


def parse(source: bytes) -> Tree:
    """Parse C++ source bytes into a Tree-sitter tree."""
    return _parser().parse(source)


def parse_root(source: bytes) -> Node:
    return parse(source).root_node
