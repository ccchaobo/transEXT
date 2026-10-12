"""SQLAlchemy models: the durable truth for documents, revisions and jobs.

Design notes tied to the spec:

* **The database is the source of truth for task state** (spec 7.3).  The
  frontend only caches what it reads from here.
* **Parse revisions are immutable.**  Re-parsing inserts a new
  ``ParseRevision`` row and moves ``Document.active_revision_id``; nothing is
  overwritten, so an annotation can always be relocated against the revision it
  was made against (spec 8).
* **A job has many attempts.**  Retrying creates a new ``Attempt`` and leaves
  the previous one as evidence (spec 7.3), rather than mutating one row.
* **Per-unit status.**  ``JobUnit`` records page/block level outcomes so a
  partial failure is visible instead of being flattened into a percentage.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "Base",
    "Document",
    "ParseRevision",
    "Page",
    "Block",
    "SourceSpanRow",
    "AssetRow",
    "Translation",
    "Term",
    "Annotation",
    "Job",
    "Attempt",
    "JobUnit",
    "JobEvent",
    "ReadingStateRow",
    "DocumentLabel",
    "LibraryCollection",
    "CollectionDocument",
    "utcnow",
]


def utcnow() -> datetime:
    """Timezone-aware UTC now.  All timestamps in the database are UTC."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


def _json_default(value: Any) -> Any:  # pragma: no cover - defensive only
    return str(value)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    format: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(80), index=True)
    #: Path of the immutable original, relative to the storage root.
    source_path: Mapped[str] = mapped_column(String(1024))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: "local" | "external" | "mixed"
    processing_mode: Mapped[str] = mapped_column(String(16), default="local")
    active_revision_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(24), default="pending")
    translate_status: Mapped[str] = mapped_column(String(24), default="pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    revisions: Mapped[list["ParseRevision"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    blocks: Mapped[list["Block"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    labels: Mapped[list["DocumentLabel"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    reading_state: Mapped["ReadingStateRow | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )


class DocumentLabel(Base):
    __tablename__ = "document_labels"
    __table_args__ = (UniqueConstraint("document_id", "label", name="uq_document_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(64))
    document: Mapped[Document] = relationship(back_populates="labels")


class LibraryCollection(Base):
    """Logical collections; membership never moves an original file."""
    __tablename__ = "library_collections"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CollectionDocument(Base):
    __tablename__ = "collection_documents"
    collection_id: Mapped[str] = mapped_column(ForeignKey("library_collections.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)


class ZoteroRecord(Base):
    """A bibliographic item can exist without any local reader attachment."""
    __tablename__="zotero_records"
    instance: Mapped[str]=mapped_column(String(64),primary_key=True)
    library_id: Mapped[int]=mapped_column(Integer,primary_key=True)
    item_key: Mapped[str]=mapped_column(String(8),primary_key=True)
    title: Mapped[str]=mapped_column(String(1024),default='')
    search_text: Mapped[str]=mapped_column(Text,default='')
    item_type: Mapped[str]=mapped_column(String(32),default='')
    parent_key: Mapped[str | None]=mapped_column(String(8),nullable=True)
    fingerprint: Mapped[str]=mapped_column(String(64))
    data: Mapped[dict[str,Any]]=mapped_column(JSON,default=dict)
    deleted: Mapped[bool]=mapped_column(Boolean,default=False)
    synced_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class ZoteroBinding(Base):
    __tablename__="zotero_bindings"
    document_id: Mapped[str]=mapped_column(ForeignKey("documents.id",ondelete="CASCADE"),primary_key=True)
    instance: Mapped[str]=mapped_column(String(64))
    library_id: Mapped[int]=mapped_column(Integer)
    item_key: Mapped[str]=mapped_column(String(8))
    attachment_key: Mapped[str | None]=mapped_column(String(8),nullable=True)
    fingerprint: Mapped[str]=mapped_column(String(64))
    baseline: Mapped[dict[str,Any]]=mapped_column(JSON,default=dict)
    synced_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class ParseRevision(Base):
    __tablename__ = "parse_revisions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    parser: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: content hash of the parser inputs, so an identical re-parse can be skipped
    input_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)

    document: Mapped[Document] = relationship(back_populates="revisions")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("revision_id", "page_index", name="uq_page_revision_index"),
        Index("ix_page_revision", "revision_id", "page_index"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("parse_revisions.id", ondelete="CASCADE"))
    page_index: Mapped[int] = mapped_column(Integer)
    printed_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    width_pt: Mapped[float] = mapped_column(Float)
    height_pt: Mapped[float] = mapped_column(Float)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    crop: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    dpi: Mapped[float] = mapped_column(Float, default=72.0)
    source_kind: Mapped[str] = mapped_column(String(16), default="text")
    preview_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    ocr_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    document: Mapped[Document] = relationship(back_populates="pages")


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("revision_id", "block_key", name="uq_block_revision_key"),
        Index("ix_block_revision_order", "revision_id", "order_index"),
        Index("ix_block_document", "document_id"),
    )

    #: The public block id (stable within a revision).
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    block_key: Mapped[str] = mapped_column(String(48))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("parse_revisions.id", ondelete="CASCADE"), index=True)
    page_index: Mapped[int] = mapped_column(Integer, index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(24))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    clean_text: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(80), default="")
    #: bumped when a human corrects recognised text (spec 5.3)
    ocr_revision: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_level: Mapped[str] = mapped_column(String(16), default="unknown")
    confidence_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    section_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    document: Mapped[Document] = relationship(back_populates="blocks")
    spans: Mapped[list["SourceSpanRow"]] = relationship(
        back_populates="block", cascade="all, delete-orphan"
    )


class SourceSpanRow(Base):
    __tablename__ = "source_spans"
    __table_args__ = (Index("ix_span_block", "block_id", "ordinal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"))
    revision_id: Mapped[str] = mapped_column(String(48), index=True)
    page_index: Mapped[int] = mapped_column(Integer, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    #: display-normalised [x0, y0, x1, y1], origin top-left of the displayed page
    bbox: Mapped[list[float]] = mapped_column(JSON)
    raw_bbox: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    raw_space: Mapped[str] = mapped_column(String(24), default="raw_pdf")
    transform: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    extra_transforms: Mapped[list[dict[str, float]]] = mapped_column(JSON, default=list)
    polygon: Mapped[list[list[float]] | None] = mapped_column(JSON, nullable=True)
    text_start: Mapped[int] = mapped_column(Integer, default=0)
    text_end: Mapped[int] = mapped_column(Integer, default=0)

    block: Mapped[Block] = relationship(back_populates="spans")


class AssetRow(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    revision_id: Mapped[str] = mapped_column(String(48), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    page_index: Mapped[int] = mapped_column(Integer, index=True)
    path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(80))
    bbox: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    caption_block_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Translation(Base):
    __tablename__ = "translations"
    __table_args__ = (
        Index("ix_translation_block", "block_id", "version"),
        Index("ix_translation_cache", "cache_key"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"), index=True)
    revision_id: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: hash of the exact source text this translation was produced from
    source_hash: Mapped[str] = mapped_column(String(80))
    source_language: Mapped[str] = mapped_column(String(16), default="en")
    target_language: Mapped[str] = mapped_column(String(16), default="zh")
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    glossary_version: Mapped[str] = mapped_column(String(64), default="none")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cache_key: Mapped[str] = mapped_column(String(80), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    origin: Mapped[str] = mapped_column(String(16), default="machine")
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: set when the source text changed after this translation was produced
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(32), default="system")


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (
        UniqueConstraint("scope", "document_id", "source", "target_language", name="uq_term_scope_source"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    source: Mapped[str] = mapped_column(String(256))
    target: Mapped[str] = mapped_column(String(256))
    target_language: Mapped[str] = mapped_column(String(16), default="zh")
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="document", index=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (Index("ix_annotation_document", "document_id", "page_index"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    revision_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    block_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    selected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    colour: Mapped[str | None] = mapped_column(String(24), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="note")
    needs_relocation: Mapped[bool] = mapped_column(Boolean, default=False)
    relocation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    migration_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReadingStateRow(Base):
    __tablename__ = "reading_states"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    page_index: Mapped[int] = mapped_column(Integer, default=0)
    block_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    view_mode: Mapped[str] = mapped_column(String(24), default="side_by_side")
    zoom: Mapped[float] = mapped_column(Float, default=1.0)
    link_scrolling: Mapped[bool] = mapped_column(Boolean, default=True)
    revision_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="reading_state")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_idempotency"),
        Index("ix_job_status", "status", "created_at"),
        Index("ix_job_document", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    type: Mapped[str] = mapped_column(String(24))
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage_status: Mapped[str] = mapped_column(String(16), default="pending")
    total_units: Mapped[int] = mapped_column(Integer, default=0)
    done_units: Mapped[int] = mapped_column(Integer, default=0)
    failed_units: Mapped[int] = mapped_column(Integer, default=0)
    skipped_units: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_estimate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    current_attempt_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    next_event_seq: Mapped[int] = mapped_column(Integer, default=0)

    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Attempt.attempt_number"
    )
    units: Mapped[list["JobUnit"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.seq"
    )


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number", name="uq_attempt_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="running")
    #: identity of the worker process holding the lease
    lease_holder: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="attempts")


class JobUnit(Base):
    """Per-page or per-block outcome, so partial success is explicit."""

    __tablename__ = "job_units"
    __table_args__ = (
        UniqueConstraint("job_id", "unit_key", name="uq_job_unit"),
        Index("ix_job_unit_status", "job_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    attempt_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    unit_key: Mapped[str] = mapped_column(String(128))
    unit_type: Mapped[str] = mapped_column(String(24), default="block")
    page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="units")


class JobEvent(Base):
    """Append-only event log; clients resume from the last seen ``seq``."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_event_seq", "job_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="events")


class Setting(Base):
    """One stored configuration value, written by the reader rather than by the environment.

    One row per key rather than one row per feature, so adding a knob later does not need a
    migration, and so the same store can hold non-translation settings when they arrive.

    It lives in the database rather than in ``.env`` for one decisive reason: **the worker
    process must see a change without being restarted.**  ``.env`` is read once at startup, so
    a key written to it is invisible to a worker that is already running - the reader would
    configure a key, see the reader still refuse to translate, and have no way to tell why.
    Both processes open this database, so both see the value on the next job.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    #: Set for values that must never be echoed back.  A flag rather than a naming convention,
    #: so a secret added later cannot leak by being forgotten in a redaction list.
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)
