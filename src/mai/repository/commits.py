from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, CommitPatch
from mai.drift.compare import subsystem_of
from mai.git.types import CommitMeta


class CommitRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def exists(self, core: str, sha: str) -> bool:
        found = await self._session.scalar(
            select(Commit.id).where(Commit.core == core, Commit.sha == sha)
        )
        return found is not None

    async def add_commit(self, core: str, meta: CommitMeta) -> bool:
        """Insert a commit + its files + patch row. Returns False if already present."""
        if await self.exists(core, meta.sha):
            return False
        commit = Commit(
            core=core, sha=meta.sha, author=meta.author, authored_at=meta.authored_at,
            committer=meta.committer, committed_at=meta.committed_at,
            message=meta.message, parent_shas=list(meta.parents), is_merge=meta.is_merge,
        )
        self._session.add(commit)
        await self._session.flush()  # populate commit.id
        self._session.add(CommitPatch(commit_id=commit.id, patch_id=meta.patch_id))
        for f in meta.files:
            self._session.add(CommitFile(
                commit_id=commit.id, path=f.path, change_type=f.change_type,
                old_path=f.old_path, added_lines=f.added, removed_lines=f.removed,
                subsystem=subsystem_of(f.path),
            ))
        return True
