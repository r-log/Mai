"""Minimal unified-diff reader — just what Gate 3 needs from a commit's patch.

Per touched file: the post-image path, the rename source (if any), and the set of
*added* line numbers in the post-image. Those line numbers map the patch onto a
Tree-sitter parse of the file at the source commit, so identifiers are read from the
AST (robust to comments / multiline) rather than scraped off raw `+` text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class FilePatch:
    new_path: str | None              # post-image path (None when deleted)
    old_path: str | None              # pre-image path (differs only on rename)
    added_lines: set[int] = field(default_factory=set)   # post-image line numbers
    is_new_file: bool = False
    is_delete: bool = False

    @property
    def renamed(self) -> bool:
        return (self.old_path is not None and self.new_path is not None
                and self.old_path != self.new_path)


def parse_patch(text: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    cur: FilePatch | None = None
    new_ln = 0
    for line in text.splitlines():
        if line.startswith("diff --git"):
            cur = FilePatch(new_path=None, old_path=None)
            files.append(cur)
            new_ln = 0
            continue
        if cur is None:
            continue
        if line.startswith("--- "):
            p = line[4:].strip()
            if p == "/dev/null":
                cur.is_new_file = True
            else:
                cur.old_path = _strip_prefix(p)
            continue
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p == "/dev/null":
                cur.is_delete = True
            else:
                cur.new_path = _strip_prefix(p)
            continue
        m = _HUNK.match(line)
        if m:
            new_ln = int(m.group(1))
            continue
        if not line:
            continue
        c = line[0]
        if c == "+":
            cur.added_lines.add(new_ln)
            new_ln += 1
        elif c == " ":
            new_ln += 1
        elif c == "\\":   # "\ No newline at end of file"
            continue
        # '-' (removed) lines do not advance the post-image counter
    return files


def _strip_prefix(path: str) -> str:
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path
