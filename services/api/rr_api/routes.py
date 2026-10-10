"""HTTP routes.  Thin on purpose: they validate input, call a service, and map the result.

No business rule lives here.  Import policy is in ``DocumentService``, parse policy in
``ParseService``, cache identity and the spend guard in ``TranslationService``, and leasing and
fencing in ``JobQueue`` — so the same rules hold whether a request arrives from the UI or from a
script.

Request and response shapes come from ``rr_contracts`` rather than being redefined here.  That
is the point of the contract package: the wire format is declared once, so this file cannot
drift from it and the generated OpenAPI document describes the real shapes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from rr_adapters.translation import describe_configuration
from rr_contracts import (
    AnnotationCreate,
    AnnotationPatch,
    AnnotationWire,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    ErrorCode,
    JobType,
    ReadingState,
    TermCreate,
    TermPatch,
    TermWire,
    TranslationRequest,
)
from rr_services import DocumentService, Settings, TranslationService
from rr_storage import (
    Annotation,
    Block,
    Document,
    Job,
    JobEvent,
    JobUnit,
    Page,
    ParseRevision,
    ReadingStateRow,
    Term,
    Translation,
    Workspace,
)

from . import reads
from .exports import export_document_markdown

router = APIRouter(prefix="/api/v1")

#: Target languages the UI offers.  This is not a limit of the provider, only of what has been
#: exercised; anything else can be passed explicitly.
TARGET_LANGUAGES = ("zh", "en", "ja", "de", "fr", "es", "ru")

#: Annotation kinds the contract permits.
ANNOTATION_KINDS = ("note", "highlight", "question")


# ---------------------------------------------------------------------------
# accessors, read from app.state so tests can point the app at a temp directory
# ---------------------------------------------------------------------------


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def _session(request: Request):
    return request.app.state.database.session()


def _queue(request: Request):
    return request.app.state.queue


def _document(session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"no document {document_id!r}")
    return document


def _require_revision(session, document: Document) -> str:
    if not document.active_revision_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": ErrorCode.PARSE_FAILED.value,
                "message": "this document has not been parsed yet, so it has no pages",
            },
        )
    return document.active_revision_id


def _page(session, document: Document, page_index: int) -> Page:
    revision_id = _require_revision(session, document)
    page = session.scalars(
        select(Page).where(Page.revision_id == revision_id, Page.page_index == page_index)
    ).first()
    if page is None:
        raise LookupError(f"document {document.id!r} has no page {page_index}")
    return page


def _token() -> str:
    return uuid.uuid4().hex[:20]


# ---------------------------------------------------------------------------
# bodies that are not part of the frozen contract
# ---------------------------------------------------------------------------


class UploadAccepted(BaseModel):
    document: DocumentSummary
    duplicate: bool
    job_id: str | None = None
    job_created: bool = False
    warnings: list[str] = []
    probe: dict | None = None


class PageView(BaseModel):
    """One page plus its blocks and their translations."""

    document_id: str
    revision_id: str
    page: dict
    blocks: list[dict]


class OutlineView(BaseModel):
    outline: list[dict]
    note: str | None = None


class TranslationPlanView(BaseModel):
    job_id: str
    job_created: bool
    planned_blocks: int
    already_cached: int
    would_be_sent: int
    skipped_non_translatable: int
    target_language: str
    provider: str
    model: str | None = None


class TranslationCorrection(BaseModel):
    text: str = Field(min_length=1)
    note: str | None = None


class TranslationCorrectionResult(BaseModel):
    translation: dict
    superseded: str
    machine_text_kept: bool = True


class DeletionResult(BaseModel):
    deleted: str
    files_removed: dict[str, int]


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=DocumentList, tags=["documents"])
def list_documents(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    with _session(request) as session:
        total = session.scalar(select(func.count()).select_from(Document)) or 0
        rows = session.scalars(
            select(Document)
            .order_by(Document.imported_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return {
            "items": [reads.document_summary(session, row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.post("/documents", status_code=201, response_model=UploadAccepted, tags=["documents"])
def upload_document(request: Request, file: UploadFile = File(...)) -> dict:
    """Import a file.

    The upload is streamed to disk and then identified from its own bytes; the declared filename
    is a hint and never a fact about the content.  Deduplication is by content hash inside the
    service — not by a key supplied here — so the same bytes cannot be stored twice even if a
    caller sends a different key, and the parse job's idempotency key is derived from the
    document and the hash rather than from the request.
    """
    service = DocumentService(_settings(request), _workspace(request), _queue(request))
    with _session(request) as session:
        result = service.import_file(
            session,
            filename=file.filename or "unnamed",
            stream=file.file,
        )
        session.commit()
        return {
            "document": reads.document_summary(session, result.document),
            "duplicate": result.duplicate,
            "job_id": result.job.id if result.job else None,
            "job_created": result.job_created,
            "warnings": list(result.warnings or []),
            "probe": (
                {
                    "format": result.probe.format,
                    "mime_type": result.probe.mime_type,
                    "extension_format": result.probe.extension_format,
                    "extension_matches": result.probe.extension_matches,
                    "warnings": list(result.probe.warnings),
                }
                if result.probe
                else None
            ),
        }


@router.get("/documents/{document_id}", response_model=DocumentDetail, tags=["documents"])
def get_document(request: Request, document_id: str) -> dict:
    with _session(request) as session:
        document = _document(session, document_id)
        revision = reads.active_revision(session, document)
        summary = reads.document_summary(session, document)
        revisions = [
            reads.revision_payload(row, is_active=row.id == document.active_revision_id)
            for row in session.scalars(
                select(ParseRevision)
                .where(ParseRevision.document_id == document_id)
                .order_by(ParseRevision.created_at)
            ).all()
        ]
        translated = summary["translated_block_count"]
        return {
            **summary,
            "revisions": revisions,
            "issues": [
                {
                    "code": (issue or {}).get("code") or "parse_issue",
                    "severity": (issue or {}).get("severity") or "warning",
                    "message": (issue or {}).get("message") or "",
                    "page_index": (issue or {}).get("page_index"),
                    "block_id": (issue or {}).get("block_id"),
                    "details": dict((issue or {}).get("details") or {}),
                }
                for issue in (revision.issues or []) if revision
            ],
            "translations_summary": {
                "translated": translated,
                "total": summary["block_count"],
                "missing": max(summary["block_count"] - translated, 0),
            },
            "capabilities": {
                # What this document can do, derived from what was actually parsed rather than
                # assumed: a scanned PDF without OCR cannot be translated at all.
                "readable": revision is not None,
                "translatable": bool(revision and summary["block_count"]),
                "has_outline": len(reads.outline(session, revision.id)) > 1 if revision else False,
                "translation_configured": describe_configuration()["configured"],
            },
            "source_url": f"/api/v1/documents/{document_id}/original",
        }


@router.delete("/documents/{document_id}", response_model=DeletionResult, tags=["documents"])
def delete_document(
    request: Request, document_id: str, confirm: bool = Query(default=False)
) -> dict:
    """Delete a document and everything derived from it.

    ``confirm=true`` is required: this removes the stored original as well as the parse, the
    translations and the annotations, and there is no undo.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": ErrorCode.INVALID_REQUEST.value,
                "message": (
                    "deletion removes the stored original too; pass confirm=true to proceed"
                ),
            },
        )
    with _session(request) as session:
        _document(session, document_id)
        session.delete(session.get(Document, document_id))
        session.commit()
        # Rows and files both go.  Deleting only the rows would leave the original text on disk
        # after the reader was told the document was deleted.
        removed = _workspace(request).delete_document(document_id)
        return {"deleted": document_id, "files_removed": removed}


@router.get("/documents/{document_id}/original", tags=["documents"])
def get_original(request: Request, document_id: str) -> FileResponse:
    """The stored original, byte for byte, exactly as imported.  Never rewritten."""
    with _session(request) as session:
        document = _document(session, document_id)
        path = _workspace(request).resolve(document.source_path)
        filename = document.filename
    if not path.is_file():
        raise LookupError(f"the stored original for {document_id!r} is missing from disk")
    return FileResponse(path, filename=filename)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


@router.get(
    "/documents/{document_id}/pages/{page_index}", response_model=PageView, tags=["reading"]
)
def get_page(
    request: Request,
    document_id: str,
    page_index: int,
    target_language: str | None = Query(default=None),
) -> dict:
    """One page with its blocks, their display-space spans and their chosen translations."""
    with _session(request) as session:
        document = _document(session, document_id)
        page = _page(session, document, page_index)
        return reads.page_payload(session, page, target_language=target_language)


@router.get("/documents/{document_id}/pages/{page_index}/{kind}.png", tags=["reading"])
def get_page_image(request: Request, document_id: str, page_index: int, kind: str) -> FileResponse:
    if kind not in {"preview", "thumbnail"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": ErrorCode.INVALID_REQUEST.value,
                "message": "kind must be 'preview' or 'thumbnail'",
            },
        )
    with _session(request) as session:
        document = _document(session, document_id)
        page = _page(session, document, page_index)
        stored = page.preview_path if kind == "preview" else page.thumbnail_path
        if not stored:
            raise LookupError(f"no {kind} has been rendered for page {page_index}")
        path = _workspace(request).resolve(stored)
    if not path.is_file():
        raise LookupError(f"the {kind} for page {page_index} is missing from disk")
    return FileResponse(path, media_type="image/png")


@router.get("/documents/{document_id}/outline", response_model=OutlineView, tags=["reading"])
def get_outline(request: Request, document_id: str) -> dict:
    with _session(request) as session:
        document = _document(session, document_id)
        revision = reads.active_revision(session, document)
        if revision is None:
            return {"outline": [], "note": "the document has not been parsed"}
        entries = reads.outline(session, revision.id)
        if len(entries) <= 1:
            # Said out loud rather than presented as navigation: a one-entry outline would
            # assert that the document has one section, which has not been established.
            return {
                "outline": entries,
                "note": (
                    "fewer than two section headings were recognised, so this is not usable as "
                    "an outline; headings may not have been separated from the paragraphs "
                    "around them in this document's typography"
                ),
            }
        return {"outline": entries}


# ---------------------------------------------------------------------------
# translation
# ---------------------------------------------------------------------------


@router.get("/translation/configuration", tags=["translation"])
def translation_configuration() -> dict:
    """What translation would use.  The API key is reported as a boolean and never echoed."""
    described = describe_configuration()
    described["target_languages"] = list(TARGET_LANGUAGES)
    if not described["configured"]:
        described["note"] = (
            "no translation provider is configured, so translation is unavailable.  Reading, "
            "anchoring and export all work.  No placeholder text is ever produced."
        )
    return described


@router.post(
    "/documents/{document_id}/translate",
    status_code=202,
    response_model=TranslationPlanView,
    tags=["translation"],
)
def start_translation(
    request: Request,
    document_id: str,
    payload: TranslationRequest = Body(default=TranslationRequest()),
) -> dict:
    """Queue a translation job.

    The job is planned before it is queued, so the response can state how many blocks would be
    sent and how many are already cached.  Nothing is sent from this endpoint — a worker does
    that — and the worker refuses an oversized plan before spending anything.
    """
    settings = _settings(request)
    configuration = describe_configuration()
    if not configuration["configured"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": ErrorCode.TRANSLATION_NOT_CONFIGURED.value,
                "message": (
                    "no translation provider is configured.  Set RR_TRANSLATION_API_KEY (and "
                    "RR_TRANSLATION_BASE_URL if the default endpoint is wrong) and restart the "
                    "server.  No placeholder or mock translation is produced."
                ),
            },
        )
    with _session(request) as session:
        document = _document(session, document_id)
        revision_id = _require_revision(session, document)

        # The contract expresses the target as a scope (document / pages / blocks).  It is
        # resolved to an explicit block list here rather than pushed down into the service, so
        # the service keeps one way of being told what to translate.
        only_block_ids: list[str] | None = None
        if payload.scope == "blocks":
            if not payload.block_ids:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error_code": ErrorCode.INVALID_REQUEST.value,
                        "message": "scope='blocks' needs block_ids",
                    },
                )
            only_block_ids = list(payload.block_ids)
        elif payload.scope == "pages":
            if not payload.page_indices:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error_code": ErrorCode.INVALID_REQUEST.value,
                        "message": "scope='pages' needs page_indices",
                    },
                )
            only_block_ids = list(
                session.scalars(
                    select(Block.id).where(
                        Block.revision_id == revision_id,
                        Block.page_index.in_(payload.page_indices),
                    )
                ).all()
            )

        service = TranslationService(settings, _workspace(request), _queue(request))
        plan = service.plan(
            session,
            document_id,
            # The contract has no source language field; English is the project default and the
            # source language of a paper is not something the reader chooses per request.
            source_language="en",
            target_language=payload.target_language,
            only_block_ids=only_block_ids,
            force=payload.force,
        )
        limit = settings.max_translation_units
        if limit and len(plan.pending_units) > limit:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": ErrorCode.BUDGET_EXCEEDED.value,
                    "message": (
                        f"this plan covers {len(plan.pending_units)} blocks and the configured "
                        f"limit is {limit}; raise RR_MAX_TRANSLATION_UNITS to proceed"
                    ),
                },
            )
        job, created = _queue(request).enqueue(
            session,
            job_type=JobType.TRANSLATE.value,
            document_id=document_id,
            request={
                "target_language": payload.target_language,
                "source_language": "en",
                "only_block_ids": only_block_ids,
                "force": payload.force,
            },
            total_units=len(plan.pending_units),
            # The same revision, language pair and block set is the same request, so it must not
            # queue twice.  A forced request is a deliberately different request and gets its own
            # key, or it would be swallowed by the job it is trying to redo.
            idempotency_key=(
                f"translate:{document_id}:{revision_id}:en:{payload.target_language}:"
                f"{len(only_block_ids) if only_block_ids else 'all'}"
                f"{':force' if payload.force else ''}"
            ),
        )
        session.commit()
        return {
            "job_id": job.id,
            "job_created": created,
            "planned_blocks": len(plan.units),
            "already_cached": len(plan.reusable_units),
            "would_be_sent": len(plan.pending_units),
            "skipped_non_translatable": plan.skipped_non_translatable,
            "target_language": payload.target_language,
            "provider": plan.provider,
            "model": plan.model,
        }


@router.get("/documents/{document_id}/translations", tags=["translation"])
def list_translations(
    request: Request,
    document_id: str,
    target_language: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    with _session(request) as session:
        _document(session, document_id)
        statement = select(Translation).where(Translation.document_id == document_id)
        if target_language:
            statement = statement.where(Translation.target_language == target_language)
        if status:
            statement = statement.where(Translation.status == status)
        rows = session.scalars(
            statement.order_by(Translation.block_id, Translation.version.desc()).limit(limit)
        ).all()
        return {
            "translations": [reads.translation_payload(row) for row in rows],
            "count": len(rows),
        }


@router.patch(
    "/translations/{translation_id}",
    response_model=TranslationCorrectionResult,
    tags=["translation"],
)
def correct_translation(
    request: Request, translation_id: str, payload: TranslationCorrection
) -> dict:
    """Record a human correction.

    A new row is written with ``origin='human'`` and a higher version instead of editing the
    machine row, so machine output is never destroyed and the original row's cache key stays
    meaningful.  The correction deliberately carries a cache key of its own, so it can never be
    mistaken for reusable machine output.
    """
    with _session(request) as session:
        original = session.get(Translation, translation_id)
        if original is None:
            raise LookupError(f"no translation {translation_id!r}")
        correction = Translation(
            id=f"tr_{_token()}",
            document_id=original.document_id,
            block_id=original.block_id,
            revision_id=original.revision_id,
            version=(original.version or 1) + 1,
            source_hash=original.source_hash,
            source_language=original.source_language,
            target_language=original.target_language,
            provider="human",
            model=None,
            prompt_version="human",
            glossary_version=original.glossary_version,
            params={"note": payload.note} if payload.note else {},
            cache_key=f"human:{original.cache_key}"[:80],
            text=payload.text,
            status="ok",
            origin="human",
            is_current=True,
            created_by="user",
        )
        session.add(correction)
        # The machine row is kept but marked not current, so "what did the service actually
        # say" remains answerable after a correction.
        original.is_current = False
        session.commit()
        return {
            "translation": reads.translation_payload(correction),
            "superseded": original.id,
            "machine_text_kept": True,
        }


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


def _job_payload(session, job: Job) -> dict:
    units = session.scalars(
        select(JobUnit).where(JobUnit.job_id == job.id).order_by(JobUnit.unit_key)
    ).all()
    finished = job.status in {"succeeded", "partially_succeeded", "failed", "cancelled"}
    progress = (
        1.0
        if finished
        else (
            min((job.done_units + job.failed_units + job.skipped_units) / job.total_units, 1.0)
            if job.total_units
            else 0.0
        )
    )
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "document_id": job.document_id,
        "stage": job.stage,
        "stage_status": job.stage_status,
        "cancel_requested": job.cancel_requested,
        "progress_fraction": progress,
        "total_units": job.total_units,
        "done_units": job.done_units,
        "failed_units": job.failed_units,
        "skipped_units": job.skipped_units,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "result": job.result,
        "usage": job.usage,
        "cost_estimate": job.cost_estimate,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "attempt_count": job.attempt_count,
        # Beyond the frozen JobWire: the per-unit detail is what tells a reader which specific
        # blocks are in doubt, and it cannot be derived from the job's counters.
        "units": [
            {
                "unit_key": unit.unit_key,
                "unit_type": unit.unit_type,
                "page_index": unit.page_index,
                "block_id": unit.block_id,
                "status": unit.status,
                "error_code": unit.error_code,
                "error_message": unit.error_message,
                "attempts_count": unit.attempts_count,
            }
            for unit in units
        ],
    }


@router.get("/jobs", tags=["jobs"])
def list_jobs(
    request: Request,
    document_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    with _session(request) as session:
        statement = select(Job)
        if document_id:
            statement = statement.where(Job.document_id == document_id)
        rows = session.scalars(statement.order_by(Job.created_at.desc()).limit(limit)).all()
        return {"jobs": [_job_payload(session, row) for row in rows]}


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(request: Request, job_id: str) -> dict:
    with _session(request) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise LookupError(f"no job {job_id!r}")
        return _job_payload(session, job)


@router.get("/jobs/{job_id}/events", tags=["jobs"])
def job_events(
    request: Request,
    job_id: str,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    """Append-only events after ``since``, so a running job can be polled without re-fetching."""
    with _session(request) as session:
        if session.get(Job, job_id) is None:
            raise LookupError(f"no job {job_id!r}")
        rows = session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > since)
            .order_by(JobEvent.seq)
            .limit(limit)
        ).all()
        return {
            "events": [
                {
                    "seq": row.seq,
                    "kind": row.kind,
                    "stage": row.stage,
                    "data": row.data,
                    # The column is `at`, not `created_at`.  The contract calls the field
                    # `created_at` because that is what the wire shape says; the mapping
                    # belongs here, in the transport layer.
                    "created_at": row.at,
                }
                for row in rows
            ],
            "last_seq": rows[-1].seq if rows else since,
        }


@router.post("/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job(request: Request, job_id: str) -> dict:
    with _session(request) as session:
        if session.get(Job, job_id) is None:
            raise LookupError(f"no job {job_id!r}")
        job = _queue(request).request_cancel(session, job_id)
        session.commit()
        # Completed work is kept: cancelling stops the job, it does not roll back translations
        # that were already produced and paid for.
        return {
            "job_id": job.id,
            "status": job.status,
            "cancel_requested": job.cancel_requested,
        }


@router.post("/jobs/{job_id}/retry", status_code=202, tags=["jobs"])
def retry_job(
    request: Request,
    job_id: str,
    unit_keys: list[str] | None = Body(default=None, embed=True),
) -> dict:
    with _session(request) as session:
        if session.get(Job, job_id) is None:
            raise LookupError(f"no job {job_id!r}")
        job, scheduled = _queue(request).retry(session, job_id, unit_keys=unit_keys)
        session.commit()
        return {"job_id": job.id, "scheduled_units": scheduled, "source_job": job_id}


# ---------------------------------------------------------------------------
# annotations
# ---------------------------------------------------------------------------


def _annotation_payload(row: Annotation) -> dict:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "revision_id": row.revision_id,
        "block_id": row.block_id,
        "page_index": row.page_index,
        "bbox": list(row.bbox) if row.bbox else None,
        "selected_text": row.selected_text,
        "note": row.note or "",
        "colour": row.colour,
        "kind": row.kind,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        # Set when a re-parse moved the text this was attached to.  The UI must surface these
        # rather than silently re-attaching an annotation somewhere plausible.
        "needs_relocation": row.needs_relocation,
        "relocation_note": row.relocation_note,
    }


@router.get("/documents/{document_id}/annotations", tags=["annotations"])
def list_annotations(request: Request, document_id: str) -> dict:
    with _session(request) as session:
        _document(session, document_id)
        rows = session.scalars(
            select(Annotation)
            .where(Annotation.document_id == document_id)
            .order_by(Annotation.page_index, Annotation.id)
        ).all()
        return {
            "annotations": [_annotation_payload(row) for row in rows],
            "needs_relocation": sum(1 for row in rows if row.needs_relocation),
        }


@router.post(
    "/documents/{document_id}/annotations",
    status_code=201,
    response_model=AnnotationWire,
    tags=["annotations"],
)
def create_annotation(request: Request, document_id: str, payload: AnnotationCreate) -> dict:
    if payload.kind not in ANNOTATION_KINDS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": ErrorCode.INVALID_REQUEST.value,
                "message": f"kind must be one of {', '.join(ANNOTATION_KINDS)}",
            },
        )
    # The contract requires the body to name the document; the path names it too.  When the two
    # disagree the request is refused rather than resolved: quietly preferring either one would
    # file the annotation under a document the caller did not mean.
    if payload.document_id != document_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": ErrorCode.INVALID_REQUEST.value,
                "message": (
                    f"body names document {payload.document_id!r} but the path names "
                    f"{document_id!r}"
                ),
            },
        )
    with _session(request) as session:
        document = _document(session, document_id)
        row = Annotation(
            id=f"an_{_token()}",
            document_id=document_id,
            revision_id=document.active_revision_id,
            block_id=payload.block_id,
            kind=payload.kind,
            page_index=payload.page_index,
            bbox=payload.bbox,
            selected_text=payload.selected_text,
            note=payload.note or "",
            colour=payload.colour,
            needs_relocation=False,
        )
        session.add(row)
        session.commit()
        return _annotation_payload(row)


@router.patch("/annotations/{annotation_id}", response_model=AnnotationWire, tags=["annotations"])
def update_annotation(request: Request, annotation_id: str, payload: AnnotationPatch) -> dict:
    with _session(request) as session:
        row = session.get(Annotation, annotation_id)
        if row is None:
            raise LookupError(f"no annotation {annotation_id!r}")
        if payload.note is not None:
            row.note = payload.note
        if payload.colour is not None:
            row.colour = payload.colour
        if payload.kind is not None:
            row.kind = payload.kind
        # A human moving an annotation clears the relocation flag: the position has been
        # re-established by a person, so it is no longer in doubt.
        if payload.bbox is not None:
            row.bbox = payload.bbox
            row.needs_relocation = False
            row.relocation_note = None
        session.commit()
        return _annotation_payload(row)


@router.delete("/annotations/{annotation_id}", tags=["annotations"])
def delete_annotation(request: Request, annotation_id: str) -> dict:
    with _session(request) as session:
        row = session.get(Annotation, annotation_id)
        if row is None:
            raise LookupError(f"no annotation {annotation_id!r}")
        session.delete(row)
        session.commit()
        return {"deleted": annotation_id}


# ---------------------------------------------------------------------------
# glossary
# ---------------------------------------------------------------------------


def _term_payload(row: Term) -> dict:
    return {
        "id": row.id,
        "source": row.source,
        "target": row.target,
        "domain": row.domain,
        "scope": row.scope,
        "document_id": row.document_id,
        "priority": row.priority,
        "mandatory": row.mandatory,
        "case_sensitive": row.case_sensitive,
        "version": row.version,
        "note": row.note,
        "updated_at": row.updated_at,
    }


@router.get("/terms", tags=["terms"])
def list_terms(
    request: Request,
    target_language: str | None = Query(default=None),
    document_id: str | None = Query(default=None),
) -> dict:
    with _session(request) as session:
        statement = select(Term)
        if target_language:
            statement = statement.where(Term.target_language == target_language)
        if document_id:
            statement = statement.where(Term.document_id == document_id)
        rows = session.scalars(statement.order_by(Term.source)).all()
        return {"terms": [_term_payload(row) for row in rows]}


@router.post("/terms", status_code=201, response_model=TermWire, tags=["terms"])
def create_term(
    request: Request,
    payload: TermCreate,
    document_id: str | None = Query(default=None),
    target_language: str = Query(default="zh"),
) -> dict:
    """Add or update a glossary term.

    Changing a term's target text bumps its ``version``, and the glossary version is part of the
    translation cache key, so affected translations are re-requested rather than silently
    reused.  The term's scope is derived from whether it names a document, which is what the
    table's uniqueness constraint is built on.
    """
    scope = "document" if (payload.document_id or document_id) else "global"
    owner = payload.document_id or document_id
    if payload.scope == "global":
        scope, owner = "global", None
    with _session(request) as session:
        if owner:
            _document(session, owner)
        existing = session.scalars(
            select(Term).where(
                Term.scope == scope,
                Term.document_id == owner,
                Term.source == payload.source,
                Term.target_language == target_language,
            )
        ).first()
        if existing is not None:
            existing.target = payload.target
            existing.note = payload.note
            existing.domain = payload.domain
            existing.priority = payload.priority
            existing.mandatory = payload.mandatory
            existing.case_sensitive = payload.case_sensitive
            # The version bump is what invalidates cached translations that used the old term.
            existing.version = (existing.version or 1) + 1
            session.commit()
            return _term_payload(existing)
        row = Term(
            id=f"tm_{_token()}",
            source=payload.source,
            target=payload.target,
            target_language=target_language,
            domain=payload.domain,
            scope=scope,
            document_id=owner,
            priority=payload.priority,
            mandatory=payload.mandatory,
            case_sensitive=payload.case_sensitive,
            note=payload.note,
            version=1,
        )
        session.add(row)
        session.commit()
        return _term_payload(row)


@router.patch("/terms/{term_id}", response_model=TermWire, tags=["terms"])
def update_term(request: Request, term_id: str, payload: TermPatch) -> dict:
    with _session(request) as session:
        row = session.get(Term, term_id)
        if row is None:
            raise LookupError(f"no term {term_id!r}")
        if payload.target is not None:
            row.target = payload.target
            row.version = (row.version or 1) + 1
        for field in ("domain", "priority", "mandatory", "case_sensitive", "note"):
            value = getattr(payload, field)
            if value is not None:
                setattr(row, field, value)
        session.commit()
        return _term_payload(row)


@router.delete("/terms/{term_id}", tags=["terms"])
def delete_term(request: Request, term_id: str) -> dict:
    with _session(request) as session:
        row = session.get(Term, term_id)
        if row is None:
            raise LookupError(f"no term {term_id!r}")
        session.delete(row)
        session.commit()
        return {"deleted": term_id}


# ---------------------------------------------------------------------------
# reading state
# ---------------------------------------------------------------------------


def _reading_payload(row: ReadingStateRow) -> dict:
    return {
        "page_index": row.page_index,
        "block_id": row.block_id,
        "view_mode": row.view_mode,
        "zoom": row.zoom,
        "link_scrolling": row.link_scrolling,
        "revision_id": row.revision_id,
        "updated_at": row.updated_at,
    }


@router.get("/documents/{document_id}/reading-state", tags=["reading"])
def get_reading_state(request: Request, document_id: str) -> dict:
    with _session(request) as session:
        _document(session, document_id)
        row = session.get(ReadingStateRow, document_id)
        return {"reading_state": _reading_payload(row) if row else None}


@router.put(
    "/documents/{document_id}/reading-state",
    response_model=ReadingState,
    tags=["reading"],
)
def put_reading_state(request: Request, document_id: str, payload: ReadingState) -> dict:
    with _session(request) as session:
        document = _document(session, document_id)
        row = session.get(ReadingStateRow, document_id)
        if row is None:
            row = ReadingStateRow(document_id=document_id)
            session.add(row)
        row.page_index = payload.page_index
        row.block_id = payload.block_id
        row.view_mode = payload.view_mode
        row.zoom = payload.zoom
        row.link_scrolling = payload.link_scrolling
        row.revision_id = payload.revision_id or document.active_revision_id
        row.updated_at = datetime.now(timezone.utc)
        document.last_opened_at = row.updated_at
        session.commit()
        return _reading_payload(row)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@router.get("/documents/{document_id}/export.md", tags=["export"])
def export_markdown(
    request: Request,
    document_id: str,
    target_language: str = Query(default="zh"),
    include_source: bool = Query(default=True),
    include_translation: bool = Query(default=True),
    outline: bool = Query(default=True),
    download: bool = Query(default=True),
) -> Response:
    """Bilingual Markdown.

    The file states in its own header that it is a reflowed transcript rather than a facsimile,
    and that the anchoring is block-level.  Those claims travel with the file so that a reader
    who only has the export still knows what it is.
    """
    with _session(request) as session:
        document = _document(session, document_id)
        markdown, filename = export_document_markdown(
            session,
            document,
            target_language=target_language,
            include_source=include_source,
            include_translation=include_translation,
            outline=outline,
        )
    headers = (
        {"Content-Disposition": f'attachment; filename="{filename}"'} if download else None
    )
    return PlainTextResponse(
        markdown, media_type="text/markdown; charset=utf-8", headers=headers
    )


__all__ = ["ANNOTATION_KINDS", "TARGET_LANGUAGES", "router"]
