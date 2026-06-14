import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
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
