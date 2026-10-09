"""API contracts and the canonical error taxonomy.

The FastAPI routers in ``services/api`` are generated from these Pydantic
models, so this module is the single source of truth for the wire format and
the OpenAPI document written to ``packages/contracts/openapi.json``.

Spec 9.1 requires every error to carry ``code``, ``message``, ``retryable``,
``scope`` and ``request_id``, and it requires the status code to be distinct
from the business code so that "unsupported file", "OCR unavailable", "external
rate limit", "insufficient balance", "conversion failed" and "result validation
failed" are all distinguishable.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "HealthResponse",
    "DocumentFormat",
    "ProcessingMode",
    "JobType",
    "JobStatus",
    "StageStatus",
    "BlockTypeWire",
    "ConfidenceWire",
    "SpanWire",
    "BlockWire",
    "PageWire",
    "IssueWire",
    "DocumentSummary",
    "DocumentDetail",
    "DocumentList",
    "TranslationRequest",
    "TranslationWire",
    "TranslationRevisionRequest",
    "BlockTextPatch",
    "AnnotationCreate",
    "AnnotationPatch",
    "AnnotationWire",
    "TermCreate",
    "TermPatch",
    "TermWire",
    "ReadingState",
    "ExportRequest",
    "JobWire",
    "JobEventWire",
    "AttemptWire",
    "ErrorCode",
    "ErrorBody",
    "ErrorResponse",
]


# ---------------------------------------------------------------------------
# Enumerations shared with the frontend
# ---------------------------------------------------------------------------


class DocumentFormat(str, Enum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    DOCX = "docx"


class ProcessingMode(str, Enum):
    """Where work happens, shown to the user before anything is sent out."""

    LOCAL = "local"
    EXTERNAL = "external"
    MIXED = "mixed"


class JobType(str, Enum):
    PARSE = "parse"
    OCR = "ocr"
    TRANSLATE = "translate"
    EXPORT = "export"
    CONVERT = "convert"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


BlockTypeWire = Literal[
    "title", "author", "affiliation", "abstract", "heading", "paragraph",
    "list_item", "caption", "table", "table_cell", "formula", "figure",
    "reference", "footnote", "header", "footer", "page_number", "unknown",
]


class ConfidenceWire(BaseModel):
    score: float | None = None
    level: Literal["high", "medium", "low", "unknown"] = "unknown"
    source: str | None = None


class SpanWire(BaseModel):
    """A source region in the frozen coordinate contract (spec 8)."""

    page_index: int = Field(ge=0)
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Display-normalised [x0, y0, x1, y1], origin top-left of the displayed page, each value in 0..1",
    )
    raw_bbox: list[float] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        description="Bounding box in the source coordinate space",
    )
    raw_space: str = "raw_pdf"
    polygon: list[list[float]] | None = None
    text_start: int = 0
    text_end: int = 0

    @field_validator("bbox", "raw_bbox")
    @classmethod
    def _check_box(cls, value: list[float] | None) -> list[float] | None:
        """Reject a box that is reversed or outside the unit square.

        Normalised coordinates are clamped to ``[0, 1]`` with ``x0 <= x1`` and
        ``y0 <= y1`` (ADR 0008).  Enforcing it at the boundary means a consumer
        never has to defend against a box it cannot draw.  The raw box is only
        checked for ordering, because raw PDF space legitimately extends beyond
        the visible page.
        """
        if value is None:
            return None
        x0, y0, x1, y1 = value
        if x1 < x0 or y1 < y0:
            raise ValueError("bbox must satisfy x0 <= x1 and y0 <= y1")
        return value

    @field_validator("bbox")
    @classmethod
    def _check_normalised_range(cls, value: list[float]) -> list[float]:
        if any(component < 0.0 or component > 1.0 for component in value):
            raise ValueError("normalised bbox values must lie in 0..1")
        return value


class BlockWire(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    page_index: int
    order: int
    type: BlockTypeWire
    text: str
    raw_text: str
    translatable: bool
    is_body: bool
    confidence: ConfidenceWire
    spans: list[SpanWire]
    pages: list[int]
    section_path: list[str] = []
    section_id: str | None = None
    level: int | None = None
    cross_page: bool = False
    human_edited: bool = False
    table: dict[str, Any] | None = None
    metadata: dict[str, Any] = {}
    translation_status: Literal["none", "pending", "ok", "failed", "stale"] = "none"


class PageWire(BaseModel):
    index: int = Field(ge=0, description="0-based internal page index")
    page_number: int = Field(ge=1, description="1-based page number shown in the UI")
    printed_label: str | None = None
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = Field(
        default=0,
        description=(
            "Page /Rotate. Only quarter turns are representable; arbitrary skew is "
            "an AffineTransform, not a rotation (ADR 0008)."
        ),
    )
    display_width: float = Field(gt=0)
    display_height: float = Field(gt=0)
    source_kind: str
    preview_url: str | None = None
    thumbnail_url: str | None = None
    text_chars: int = Field(default=0, ge=0)
    ocr_applied: bool
    warnings: list[str] = []


class IssueWire(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    page_index: int | None = None
    block_id: str | None = None
    details: dict[str, Any] = {}


class ParseRevisionWire(BaseModel):
    id: str
    parser: str
    parser_version: str
    status: str
    created_at: datetime
    parameters: dict[str, Any] = {}
    warnings: list[str] = []
    stats: dict[str, Any] = {}
    is_active: bool = False


class TranslationWire(BaseModel):
    id: str
    block_id: str
    text: str
    language: str
    target_language: str
    provider: str
    model: str | None = None
    version: int
    status: Literal["ok", "failed", "partial"]
    origin: Literal["machine", "human", "imported"]
    stale: bool = False
    error_code: str | None = None
    created_at: datetime
    usage: dict[str, Any] = {}
    issues: list[IssueWire] = []


class AssetWire(BaseModel):
    id: str
    kind: str
    page_index: int
    url: str
    bbox: list[float] | None = None
    caption_block_id: str | None = None
    width: int | None = None
    height: int | None = None


class DocumentSummary(BaseModel):
    id: str
    filename: str
    format: DocumentFormat
    size_bytes: int
    sha256: str
    page_count: int | None = None
    imported_at: datetime
    processing_mode: ProcessingMode
    active_revision_id: str | None = None
    parse_status: StageStatus
    translate_status: StageStatus
    progress: float = Field(ge=0.0, le=1.0)
    warning_count: int = 0
    error_count: int = 0
    last_opened_at: datetime | None = None
    labels: list[str] = []
    title: str | None = None


class DocumentDetail(DocumentSummary):
    revisions: list[ParseRevisionWire] = []
    issues: list[IssueWire] = []
    translations_summary: dict[str, int] = {}
    capabilities: dict[str, bool] = {}
    source_url: str


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class TranslationRequest(BaseModel):
    """Request translation for pages, blocks or the whole document."""

    scope: Literal["document", "pages", "blocks"] = "pages"
    page_indices: list[int] | None = None
    block_ids: list[str] | None = None
    target_language: str = "zh"
    provider: str | None = None
    model: str | None = None
    glossary_ids: list[str] | None = None
    force: bool = Field(
        default=False,
        description="Re-translate even when a current cached result exists",
    )
    budget_limit: float | None = Field(
        default=None, description="Maximum estimated spend for this job, in provider units"
    )
    max_concurrency: int | None = Field(default=None, ge=1, le=16)
    preserve_human_edits: bool = True


class TranslationRevisionRequest(BaseModel):
    text: str
    note: str | None = None
    base_version: int | None = Field(
        default=None, description="Optimistic concurrency: reject if the current version differs"
    )


class BlockTextPatch(BaseModel):
    """Correct OCR text for a block (spec 5.3)."""

    text: str
    base_revision: int | None = Field(
        default=None, description="Optimistic concurrency on the block's OCR revision"
    )
    invalidate_translations: bool = True


class AnnotationCreate(BaseModel):
    document_id: str
    revision_id: str | None = None
    block_id: str | None = None
    page_index: int | None = None
    bbox: list[float] | None = None
    selected_text: str | None = None
    note: str = ""
    colour: str | None = None
    kind: Literal["note", "highlight", "question"] = "note"


class AnnotationPatch(BaseModel):
    note: str | None = None
    colour: str | None = None
    kind: Literal["note", "highlight", "question"] | None = None
    block_id: str | None = None
    page_index: int | None = None
    bbox: list[float] | None = None


class AnnotationWire(BaseModel):
    id: str
    document_id: str
    revision_id: str | None = None
    block_id: str | None = None
    page_index: int | None = None
    bbox: list[float] | None = None
    selected_text: str | None = None
    note: str
    colour: str | None = None
    kind: str
    created_at: datetime
    updated_at: datetime
    needs_relocation: bool = False
    relocation_note: str | None = None


class TermCreate(BaseModel):
    source: str
    target: str
    domain: str | None = None
    scope: Literal["global", "document"] = "document"
    document_id: str | None = None
    priority: int = 0
    mandatory: bool = False
    case_sensitive: bool = False
    note: str | None = None


class TermPatch(BaseModel):
    target: str | None = None
    domain: str | None = None
    priority: int | None = None
    mandatory: bool | None = None
    case_sensitive: bool | None = None
    note: str | None = None
    base_version: int | None = None


class TermWire(BaseModel):
    id: str
    source: str
    target: str
    domain: str | None = None
    scope: str
    document_id: str | None = None
    priority: int = 0
    mandatory: bool = False
    case_sensitive: bool = False
    version: int = 1
    note: str | None = None
    updated_at: datetime


class ReadingState(BaseModel):
    """Where the reader was in a document.

    The document is identified by the request path, not by a field here, so this
    payload cannot disagree with the URL it was sent to.
    """

    page_index: int = Field(default=0, ge=0)
    block_id: str | None = None
    view_mode: Literal["side_by_side", "source_only", "target_only", "stacked"] = "side_by_side"
    zoom: float = Field(default=1.0, gt=0.0, le=8.0)
    link_scrolling: bool = True
    revision_id: str | None = None
    updated_at: datetime | None = None


class ExportRequest(BaseModel):
    target_language: str = "zh"
    include_source: bool = True
    include_annotations: bool = True
    include_glossary: bool = True
    page_range: list[int] | None = None
    revision_id: str | None = None
    translation_revision: Literal["prefer_human", "machine_only", "human_only"] = "prefer_human"


class AttemptWire(BaseModel):
    id: str
    attempt_number: int
    status: JobStatus
    lease_holder: str | None = None
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class JobProgressWire(BaseModel):
    stage: str
    total_units: int = 0
    done_units: int = 0
    failed_units: int = 0
    skipped_units: int = 0
    message: str | None = None


class JobWire(BaseModel):
    id: str
    type: JobType
    status: JobStatus
    document_id: str | None = None
    stage: str | None = None
    progress: JobProgressWire | None = None
    percent: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempt_count: int = 0
    current_attempt_id: str | None = None
    idempotency_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = {}
    cost_estimate: dict[str, Any] = {}
    cancellable: bool = True
    retryable: bool = False
    failures: list[dict[str, Any]] = []


class JobEventWire(BaseModel):
    """One increment on the job event stream (spec 9.1: resumable)."""

    seq: int
    job_id: str
    at: datetime
    kind: Literal[
        "job_created", "stage_started", "stage_finished", "unit_done", "unit_failed",
        "progress", "job_finished", "job_failed", "job_cancelled", "log",
    ]
    stage: str | None = None
    data: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorCode(str, Enum):
    """Business error codes, deliberately separate from HTTP status."""

    # request / upload
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_FORMAT = "unsupported_format"
    LEGACY_DOC_UNSUPPORTED = "legacy_doc_unsupported"
    FILE_TOO_LARGE = "file_too_large"
    TOO_MANY_PAGES = "too_many_pages"
    EMPTY_FILE = "empty_file"
    CORRUPT_FILE = "corrupt_file"
    ENCRYPTED_FILE = "encrypted_file"
    DECOMPRESSION_BOMB = "decompression_bomb"
    DUPLICATE_DOCUMENT = "duplicate_document"

    # capabilities
    OCR_UNAVAILABLE = "ocr_unavailable"
    CONVERTER_UNAVAILABLE = "converter_unavailable"
    TRANSLATION_NOT_CONFIGURED = "translation_not_configured"

    # processing
    PARSE_FAILED = "parse_failed"
    OCR_FAILED = "ocr_failed"
    CONVERSION_FAILED = "conversion_failed"
    VALIDATION_FAILED = "validation_failed"
    TRANSLATION_FAILED = "translation_failed"
    PLACEHOLDER_LOSS = "placeholder_loss"

    # external services
    EXTERNAL_TIMEOUT = "external_timeout"
    EXTERNAL_RATE_LIMITED = "external_rate_limited"
    EXTERNAL_AUTH_FAILED = "external_auth_failed"
    EXTERNAL_INSUFFICIENT_BALANCE = "external_insufficient_balance"
    EXTERNAL_UNAVAILABLE = "external_unavailable"
    EXTERNAL_UNKNOWN_RESULT = "external_unknown_result"

    # local state
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    STALE_REVISION = "stale_revision"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    INTERNAL = "internal_error"


#: Which codes are worth retrying without human intervention.
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.EXTERNAL_TIMEOUT,
        ErrorCode.EXTERNAL_RATE_LIMITED,
        ErrorCode.EXTERNAL_UNAVAILABLE,
        ErrorCode.EXTERNAL_UNKNOWN_RESULT,
        ErrorCode.OCR_FAILED,
        ErrorCode.PARSE_FAILED,
        ErrorCode.TRANSLATION_FAILED,
        ErrorCode.INTERNAL,
    }
)

#: Default HTTP status per business code.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.LEGACY_DOC_UNSUPPORTED: 415,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.TOO_MANY_PAGES: 413,
    ErrorCode.EMPTY_FILE: 400,
    ErrorCode.CORRUPT_FILE: 422,
    ErrorCode.ENCRYPTED_FILE: 422,
    ErrorCode.DECOMPRESSION_BOMB: 422,
    ErrorCode.DUPLICATE_DOCUMENT: 409,
    ErrorCode.OCR_UNAVAILABLE: 503,
    ErrorCode.CONVERTER_UNAVAILABLE: 503,
    ErrorCode.TRANSLATION_NOT_CONFIGURED: 503,
    ErrorCode.PARSE_FAILED: 422,
    ErrorCode.OCR_FAILED: 422,
    ErrorCode.CONVERSION_FAILED: 422,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.TRANSLATION_FAILED: 502,
    ErrorCode.PLACEHOLDER_LOSS: 422,
    ErrorCode.EXTERNAL_TIMEOUT: 504,
    ErrorCode.EXTERNAL_RATE_LIMITED: 429,
    ErrorCode.EXTERNAL_AUTH_FAILED: 502,
    ErrorCode.EXTERNAL_INSUFFICIENT_BALANCE: 402,
    ErrorCode.EXTERNAL_UNAVAILABLE: 503,
    ErrorCode.EXTERNAL_UNKNOWN_RESULT: 502,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.STALE_REVISION: 409,
    ErrorCode.BUDGET_EXCEEDED: 402,
    ErrorCode.CANCELLED: 409,
    ErrorCode.INTERNAL: 500,
}


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool
    scope: str = Field(description="What the error applies to: request, document, job, block, ...")
    request_id: str | None = None
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: str
    worker: str
    capabilities: dict[str, Any]
    warnings: list[str] = []
