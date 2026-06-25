"""mai's purpose-built C++ parser (Tree-sitter engine, task-scoped extraction).

Adopts GITA's parsing pattern but is C++-only and answers only what the
portability gates need. See extract.py for the public surface.
"""
from mai.cppindex.extract import (
    CppFunction,
    file_symbols,
    find_function,
    function_covering,
    functions,
)

__all__ = [
    "CppFunction",
    "file_symbols",
    "find_function",
    "function_covering",
    "functions",
]
