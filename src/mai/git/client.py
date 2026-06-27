import asyncio
import shutil
from pathlib import Path
from typing import Protocol

from mai.git.types import CommitFileMeta, CommitMeta


class GitError(RuntimeError):
    """A git subprocess returned non-zero."""


class GitClient(Protocol):
    async def ensure_mirror(self, core: str, url: str) -> None: ...
    async def fetch(self, core: str) -> None: ...
    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]: ...
    async def diff(self, core: str, sha: str) -> str: ...
    async def read_file(self, core: str, ref: str, path: str) -> str | None: ...
    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]: ...
    async def ensure_worktree(self, core: str) -> str: ...
    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str: ...
    async def head_sha(self, core: str) -> str: ...
    async def apply_fraction(self, core: str, patch_text: str,
                             paths: list[str]) -> tuple[int, int]: ...
    async def rejected_hunks(self, core: str, patch_text: str,
                             paths: list[str]) -> dict[str, str]: ...
    async def read_region(self, core: str, path: str, start: int, end: int) -> str: ...
    async def log_touching(self, core: str, paths: list[str], *,
                           limit: int = 80) -> list[dict]: ...


class LocalGitClient:
    """Production GitClient: async subprocess over bare `--mirror` clones under mirror_dir."""

    def __init__(self, mirror_dir: str, worktree_dir: str | None = None):
        self._root = Path(mirror_dir)
        wt_root = (Path(worktree_dir) if worktree_dir
                   else self._root.parent / "worktrees")
        # MUST be absolute: `git -C <mirror> worktree add <path>` resolves a relative
        # <path> against the mirror dir, but apply_check's `git -C <path> apply`
        # resolves it against the process cwd. A relative path makes those disagree
        # (worktree created inside the mirror, applies run against an empty dir).
        self._wt_root = wt_root.resolve()
        # Per-core caches that make ensure_worktree a no-op on the hot path: a
        # clean worktree at a known HEAD needs no reset/clean. Only apply_fraction's
        # `--reject` dirties the tree (apply --check never writes), and only fetch
        # moves HEAD -- both invalidate below. This collapses ~4 git spawns/check
        # (config+rev-parse+reset+clean) to zero, the dominant cost on Windows.
        self._configured: set[str] = set()   # core.autocrlf/fsmonitor set once
        self._head: dict[str, str] = {}       # cached HEAD per core
        self._dirty: set[str] = set()         # cores whose worktree needs reset

    def _path(self, core: str) -> Path:
        return self._root / f"{core}.git"

    async def _run_raw(self, args: list[str], *,
                       stdin: bytes | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(input=stdin)
        return (proc.returncode, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    async def _run(self, args: list[str], *, stdin: bytes | None = None) -> str:
        rc, out, err = await self._run_raw(args, stdin=stdin)
        if rc != 0:
            raise GitError(f"git {' '.join(args)} -> {rc}: {err.strip()}")
        return out

    async def _git(self, core: str, *args: str, stdin: bytes | None = None) -> str:
        return await self._run(["-C", str(self._path(core)), *args], stdin=stdin)

    async def ensure_mirror(self, core: str, url: str) -> None:
        path = self._path(core)
        if (path / "HEAD").exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(["clone", "--mirror", url, str(path)])

    async def fetch(self, core: str) -> None:
        await self._git(core, "fetch", "--prune")
        # HEAD may have moved; force the next ensure_worktree to re-read + reset.
        self._head.pop(core, None)
        self._dirty.add(core)

    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]:
        rng = "HEAD" if since_sha is None else f"{since_sha}..HEAD"
        out = await self._git(core, "rev-list", "--reverse", rng)
        shas = [s for s in out.splitlines() if s]
        return [await self._commit_meta(core, s) for s in shas]

    async def _commit_meta(self, core: str, sha: str) -> CommitMeta:
        # structured header fields (no body — body fetched separately to avoid newline clashes)
        fmt = "%H%n%an%n%aI%n%cn%n%cI%n%P"
        head = (await self._git(core, "show", "-s", f"--format={fmt}", sha)).split("\n")
        if len(head) < 6:
            raise GitError(f"unexpected git show output for {sha}: {head!r}")
        full_sha, an, aiso, cn, ciso, parents_line = head[0], head[1], head[2], head[3], head[4], head[5]
        parents = parents_line.split() if parents_line.strip() else []
        is_merge = len(parents) > 1
        message = (await self._git(core, "show", "-s", "--format=%B", sha)).rstrip("\n")
        patch_id = None
        files: list[CommitFileMeta] = []
        if not is_merge:
            patch_id = await self._patch_id(core, sha)
            files = await self._files(core, sha)
        return CommitMeta(sha=full_sha, author=an, authored_at=aiso, committer=cn,
                          committed_at=ciso, message=message, parents=parents,
                          is_merge=is_merge, patch_id=patch_id, files=files)

    async def _patch_id(self, core: str, sha: str) -> str | None:
        patch = await self._git(core, "diff-tree", "--root", "-p", "-M", sha)
        if not patch.strip():
            return None
        out = await self._git(core, "patch-id", "--stable",
                              stdin=patch.encode("utf-8", "replace"))
        parts = out.split()
        return parts[0] if parts else None

    async def _files(self, core: str, sha: str) -> list[CommitFileMeta]:
        # name-status and numstat are emitted in the SAME file order by the same diff walk,
        # so we zip them by index (robust against paths with spaces).
        names = (await self._git(core, "show", "-M", "--name-status", "--format=", sha)).strip("\n")
        nums = (await self._git(core, "show", "-M", "--numstat", "--format=", sha)).strip("\n")
        name_rows = [r for r in names.split("\n") if r]
        num_rows = [r for r in nums.split("\n") if r]
        if len(name_rows) != len(num_rows):
            raise GitError(f"name-status/numstat mismatch for {sha}: "
                           f"{len(name_rows)} vs {len(num_rows)}")
        files: list[CommitFileMeta] = []
        for name_line, num_line in zip(name_rows, num_rows):
            nparts = name_line.split("\t")
            change = nparts[0][0]                       # A | M | D | R | C | T
            if change in ("R", "C") and len(nparts) >= 3:
                old_path, path = nparts[1], nparts[2]
            else:
                old_path, path = None, nparts[-1]
            cols = num_line.split("\t")
            added = int(cols[0]) if cols[0].isdigit() else 0   # "-" for binary
            removed = int(cols[1]) if cols[1].isdigit() else 0
            files.append(CommitFileMeta(path=path, change_type=change,
                                        old_path=old_path, added=added, removed=removed))
        return files

    async def diff(self, core: str, sha: str) -> str:
        """The commit's unified patch (same diff that feeds patch-id)."""
        return await self._git(core, "diff-tree", "--root", "-p", "-M", sha)

    async def read_file(self, core: str, ref: str, path: str) -> str | None:
        """The text of a blob at `ref:path`, or None if it does not exist there."""
        rc, out, _ = await self._run_raw(
            ["-C", str(self._path(core)), "show", f"{ref}:{path}"])
        return out if rc == 0 else None

    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]:
        """Whether each path exists in the core's HEAD tree.

        One `cat-file --batch-check` spawn for all paths (output is one line per
        input, in order; a missing object's line ends in ' missing'), instead of
        one `cat-file -e` spawn per path -- the spawn count dominates on Windows.
        """
        result: dict[str, bool] = {p: False for p in paths}
        if not paths:
            return result
        stdin = "".join(f"HEAD:{p}\n" for p in paths).encode("utf-8", "replace")
        _, out, _ = await self._run_raw(
            ["-C", str(self._path(core)), "cat-file", "--batch-check"], stdin=stdin)
        for p, line in zip(paths, out.splitlines()):
            result[p] = not line.endswith("missing")
        return result

    async def ensure_worktree(self, core: str) -> str:
        """A working tree of the bare mirror checked out at HEAD (reset on refresh).

        Returns the worktree path. The bare mirror shares its object store with the
        worktree, so after a fetch the new objects are present and the worktree is
        reset to the mirror's current HEAD.

        Hot path: an existing, clean worktree at the cached HEAD returns immediately
        with no git spawn. A reset/clean runs only on first creation, after a fetch
        moved HEAD, or after apply_fraction's `--reject` dirtied the tree.
        """
        if core not in self._configured:
            await self._git(core, "config", "core.autocrlf", "false")
            await self._git(core, "config", "core.fsmonitor", "false")
            self._configured.add(core)
        head = self._head.get(core)
        if head is None:
            head = (await self._git(core, "rev-parse", "HEAD")).strip()
            self._head[core] = head
        wt = self._wt_root / core
        if (wt / ".git").exists() and core not in self._dirty:
            return str(wt)
        if (wt / ".git").exists():
            await self._run(["-C", str(wt), "reset", "--hard", head])
            await self._run(["-C", str(wt), "clean", "-fdq"])
            self._dirty.discard(core)
        else:
            # Self-heal a stale/half-created/corrupt worktree (e.g. left by a crashed
            # run): remove any stray working dir AND the registration's admin entry
            # (<mirror>.git/worktrees/<core>) directly, since `prune` keeps dangling
            # entries for gc.worktreePruneExpire (3 months) and skips corrupt ones.
            if wt.exists():
                shutil.rmtree(wt, ignore_errors=True)
            admin = self._path(core) / "worktrees" / core
            if admin.exists():
                shutil.rmtree(admin, ignore_errors=True)
            await self._git(core, "worktree", "prune", "--expire=now")
            wt.parent.mkdir(parents=True, exist_ok=True)
            await self._git(core, "worktree", "add", "--detach", "--force",
                            str(wt), head)
            self._dirty.discard(core)
        return str(wt)

    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str:
        """Grade `git apply --check` of a patch against the core's worktree.

        Returns 'clean' | 'reverse_clean' | 'conflict' | 'file_absent'. Never raises
        on a non-applying patch — the result is the signal.
        """
        wt = await self.ensure_worktree(core)
        args = ["-C", wt, "apply", "--check"]
        if reverse:
            args.append("--reverse")
        args.append("-")
        rc, _, err = await self._run_raw(args, stdin=patch_text.encode("utf-8", "replace"))
        if rc == 0:
            return "reverse_clean" if reverse else "clean"
        low = err.lower()
        if "no such file" in low or "does not exist" in low:
            return "file_absent"
        return "conflict"

    async def head_sha(self, core: str) -> str:
        return (await self._git(core, "rev-parse", "HEAD")).strip()

    async def apply_fraction(self, core: str, patch_text: str,
                             paths: list[str]) -> tuple[int, int]:
        """How much of a (conflicting) patch applies, in hunks: (applied, total).

        Runs `git apply --reject` (lands clean hunks, writes <file>.rej for the rest),
        then counts hunk headers in the patch vs in the .rej files for `paths`.
        total == 0 (binary / no hunks) -> (0, 0). Never raises on a non-applying patch.
        """
        total = sum(1 for ln in patch_text.splitlines() if ln.startswith("@@ "))
        if total == 0:
            return (0, 0)
        wt = await self.ensure_worktree(core)
        await self._run_raw(["-C", wt, "apply", "--reject", "-"],
                            stdin=patch_text.encode("utf-8", "replace"))
        # `--reject` writes clean hunks + .rej files into the tree: it is now dirty,
        # so the next ensure_worktree for this core must reset before reuse.
        self._dirty.add(core)
        rejected = 0
        for p in paths:
            rej = Path(wt) / (p + ".rej")
            if rej.exists():
                rejected += sum(1 for ln in rej.read_text("utf-8", "replace").splitlines()
                                if ln.startswith("@@ "))
        return (max(0, total - rejected), total)

    async def rejected_hunks(self, core: str, patch_text: str,
                             paths: list[str]) -> dict[str, str]:
        """Apply the patch with --reject; return {path: rej_text} (the hunks git
        could not place). Dirties the worktree (next ensure_worktree resets)."""
        wt = await self.ensure_worktree(core)
        await self._run_raw(["-C", wt, "apply", "--reject", "-"],
                            stdin=patch_text.encode("utf-8", "replace"))
        self._dirty.add(core)
        out: dict[str, str] = {}
        for p in paths:
            rej = Path(wt) / (p + ".rej")
            out[p] = rej.read_text("utf-8", "replace") if rej.exists() else ""
        return out

    async def read_region(self, core: str, path: str, start: int, end: int) -> str:
        """Lines [start, end] (1-based inclusive) of HEAD:path; '' if absent."""
        rc, content, _ = await self._run_raw(
            ["-C", str(self._path(core)), "show", f"HEAD:{path}"])
        if rc != 0:
            return ""
        lines = content.splitlines()
        return "\n".join(lines[max(0, start - 1):max(0, end)])

    async def log_touching(self, core: str, paths: list[str], *,
                           limit: int = 80) -> list[dict]:
        """Recent non-merge commits touching any of `paths`: [{sha, date, title}]."""
        if not paths:
            return []
        rc, out, _ = await self._run_raw(
            ["-C", str(self._path(core)), "log", "--no-merges",
             f"-n{limit}", "--format=%H%x09%cI%x09%s", "--", *paths])
        if rc != 0:
            return []
        rows: list[dict] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                rows.append({"sha": parts[0][:10], "date": parts[1][:10], "title": parts[2]})
        return rows
