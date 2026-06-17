from dataclasses import dataclass, field


@dataclass
class CommitFileMeta:
    path: str
    change_type: str            # A | M | D | R | C | T
    old_path: str | None = None
    added: int = 0
    removed: int = 0


@dataclass
class CommitMeta:
    sha: str
    author: str
    authored_at: str            # ISO-8601 string
    committer: str
    committed_at: str
    message: str                # full commit body
    parents: list[str] = field(default_factory=list)
    is_merge: bool = False
    patch_id: str | None = None
    files: list[CommitFileMeta] = field(default_factory=list)
