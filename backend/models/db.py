"""SQLAlchemy ORM models."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    repo = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    state = Column(String, nullable=False)
    strategy = Column(String, nullable=False, default="single_agent")
    base_ref = Column(String, nullable=False)
    branch = Column(String, nullable=True)
    worktree_path = Column(String, nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class EventRow(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, nullable=False, unique=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    kind = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    payload = Column(Text, nullable=False)  # JSON


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    description = Column(Text, nullable=False)
    proposed_action = Column(Text, nullable=True)
    requested_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(String, nullable=True)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    disk_path = Column(String, nullable=False)
    phase = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


class DiffSnapshotRow(Base):
    __tablename__ = "diff_snapshots"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    snapshot_at = Column(DateTime, nullable=False)
    diff_json = Column(Text, nullable=False)  # JSON
