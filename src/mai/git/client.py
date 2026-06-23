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
    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]: ...
    async def ensure_worktree(self, core: str) -> str: ...
    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str: ...
    async def head_sha(self, core: str) -> str: ...


class LocalGitClient:
    """Production GitClient: async subprocess over bare `--mirror` clones under mirror_dir."""

    def __init__(self, mirror_dir: str, worktree_dir: str | None = None):
        self._root = Path(mirror_dir)
        self._wt_root = (Path(worktree_dir) if worktree_dir
                         else self._root.parent / "worktrees")

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

    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]:
        """Whether each path exists in the core's HEAD tree."""
        result: dict[str, bool] = {}
        for p in paths:
            rc, _, _ = await self._run_raw(
                ["-C", str(self._path(core)), "cat-file", "-e", f"HEAD:{p}"])
            result[p] = rc == 0
        return result

    async def ensure_worktree(self, core: str) -> str:
        """A working tree of the bare mirror checked out at HEAD (reset on refresh).

        Returns the worktree path. The bare mirror shares its object store with the
        worktree, so after a fetch the new objects are present and the worktree is
        reset to the mirror's current HEAD.
        """
        await self._git(core, "config", "core.autocrlf", "false")
        head = (await self._git(core, "rev-parse", "HEAD")).strip()
        wt = self._wt_root / core
        if (wt / ".git").exists():
            await self._run(["-C", str(wt), "reset", "--hard", head])
            await self._run(["-C", str(wt), "clean", "-fdq"])
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
