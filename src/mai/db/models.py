import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from mai.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String(255), unique=True)
    core: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class SourceRecord(Base):
    """Immutable, append-only verbatim copy of a fetched artifact."""
    __tablename__ = "source_record"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_type: Mapped[str] = mapped_column(String(32))   # ips | gh_issue | gh_pr | gh_commit
    source_id: Mapped[str] = mapped_column(String(255))    # immutable id, e.g. r1842
    repo_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "content_hash",
                         name="uq_source_record_identity"),
    )


class Report(Base):
    """Derived, recomputable canonical bug/finding."""
    __tablename__ = "report"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    canonical_key: Mapped[str] = mapped_column(String(255), unique=True)
    core: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ReportSourceMap(Base):
    __tablename__ = "report_source_map"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_rsm_source"),
    )


class Event(Base):
    """Immutable temporal change log."""
    __tablename__ = "event"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("report.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))   # ingested | status_changed | retracted
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(default=_now)


class SyncCursor(Base):
    """Per-repo, per-source incremental fetch cursor (temporal)."""
    __tablename__ = "sync_cursor"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repo_full_name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32))
    last_updated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("repo_full_name", "source_type", name="uq_sync_cursor"),
    )


class Enrichment(Base):
    """Derived, recomputable AI-structured view of a report. Beside the raw, never over it."""
    __tablename__ = "enrichment"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("report_id", "model", "prompt_version", "schema_version",
                         "input_hash", name="uq_enrichment_key"),
    )


class Embedding(Base):
    """Derived vector for a report's embed-text. Stored as JSON (pgvector swap later)."""
    __tablename__ = "embedding"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("report_id", "model", "input_hash", name="uq_embedding_key"),
    )


class Correlation(Base):
    """Derived edge: report_id (a bug) is related to related_report_id (a PR/issue)."""
    __tablename__ = "correlation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    related_report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    method: Mapped[str] = mapped_column(String(32))   # explicit_ref | embedding
    score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("report_id", "related_report_id", "method",
                         name="uq_correlation"),
    )


class Verification(Base):
    """Derived verdict for a bug report. One current row per report (upserted)."""
    __tablename__ = "verification"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"), unique=True)
    verdict: Mapped[str] = mapped_column(String(32))   # open | likely_fixed | fixed_confirmed
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class DriftObservation(Base):
    """Derived per-subsystem divergence between two forks (one row per pair+subsystem)."""
    __tablename__ = "drift_obs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    fork_a: Mapped[str] = mapped_column(String(255))
    fork_b: Mapped[str] = mapped_column(String(255))
    subsystem: Mapped[str] = mapped_column(String(255))
    shared: Mapped[int] = mapped_column(Integer, default=0)
    diverged: Mapped[int] = mapped_column(Integer, default=0)
    identical: Mapped[int] = mapped_column(Integer, default=0)
    only_a: Mapped[int] = mapped_column(Integer, default=0)
    only_b: Mapped[int] = mapped_column(Integer, default=0)
    observed_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("fork_a", "fork_b", "subsystem", name="uq_drift_obs"),
    )


class Commit(Base):
    """Raw, append-only code truth: one git commit on a fork's default branch."""
    __tablename__ = "commit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    core: Mapped[str] = mapped_column(String(64))
    sha: Mapped[str] = mapped_column(String(40))
    author: Mapped[str] = mapped_column(String(255))
    authored_at: Mapped[str] = mapped_column(String(40))
    committer: Mapped[str] = mapped_column(String(255))
    committed_at: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    parent_shas: Mapped[list] = mapped_column(JSON, default=list)
    is_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("core", "sha", name="uq_commit_identity"),
    )


class CommitFile(Base):
    """Raw per-file change within a commit (diffstat + rename + subsystem)."""
    __tablename__ = "commit_file"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    commit_id: Mapped[str] = mapped_column(ForeignKey("commit.id"))
    path: Mapped[str] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(4))   # A | M | D | R | C | T
    old_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_lines: Mapped[int] = mapped_column(Integer, default=0)
    removed_lines: Mapped[int] = mapped_column(Integer, default=0)
    subsystem: Mapped[str] = mapped_column(String(255))


class CommitPatch(Base):
    """Raw patch identity for a (non-merge) commit. patch_id is git's; the rest is reserved."""
    __tablename__ = "commit_patch"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    commit_id: Mapped[str] = mapped_column(ForeignKey("commit.id"), unique=True)
    patch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # git patch-id --stable
    normalized_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Phase-2 fallback
    aggregate_of: Mapped[str | None] = mapped_column(String(255), nullable=True)    # Phase-2 PR-aggregate


class PatchGroup(Base):
    """Derived: a canonical fix identity, keyed by git patch-id; members span forks."""
    __tablename__ = "patch_group"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_id: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Propagation(Base):
    """Derived: whether a fix (patch_group) is present in a core, and how we know."""
    __tablename__ = "propagation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_group_id: Mapped[str] = mapped_column(ForeignKey("patch_group.id"))
    core: Mapped[str] = mapped_column(String(64))
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    via: Mapped[str | None] = mapped_column(String(40), nullable=True)   # patch_id | cherry_trailer | "a+b"
    confidence: Mapped[str] = mapped_column(String(16), default="high")  # high | medium
    source_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("patch_group_id", "core", name="uq_propagation"),
    )


class SubsystemClass(Base):
    """Derived: a subsystem's portability class. Auto-classified, manually overridable."""
    __tablename__ = "subsystem_class"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subsystem: Mapped[str] = mapped_column(String(255), unique=True)
    classification: Mapped[str] = mapped_column(String(16))   # shared | expansion | mixed
    source: Mapped[str] = mapped_column(String(16))           # seed | heuristic | ai | manual_override
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
