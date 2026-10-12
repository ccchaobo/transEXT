"""Importing documents.

The order of operations here is deliberate and load-bearing:

1. stream the upload to disk with a **size cap enforced during the copy**, so an oversized
   upload is refused before it fills the disk rather than after;
2. verify what the bytes actually are (see :mod:`.formats`) — the filename is a claim, the
   content is the evidence;
3. for a PDF, read the page count and encryption state, so an encrypted or absurdly long
   document is refused while the user is still watching;
4. only then record the document and queue the parse.

Anything that fails at steps 2-3 removes the just-written copy.  A rejected import must
leave nothing behind, or a user who repeatedly picks the wrong file accumulates orphans
they cannot see.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from rr_contracts import DocumentFormat, ErrorCode, JobType, ProcessingMode
from rr_storage import Document, Job, JobQueue, Workspace
from rr_adapters.pdf_extract import PdfCorruptError, inspect_pdf

from .formats import FileRejected, FormatProbe, verify
from .settings import Settings


class _SizeLimitedReader:
    """Wraps an upload stream and refuses the moment it exceeds the cap.

    The cap has to be enforced *during* the copy.  Checking the length afterwards means
    the whole oversized file has already been written, which is exactly the disk-filling
    outcome the limit exists to prevent.
    """

    def __init__(self, stream: BinaryIO, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        if chunk:
            self.bytes_read += len(chunk)
            if self.bytes_read > self._limit:
                raise FileRejected(
                    ErrorCode.FILE_TOO_LARGE,
                    f"the file is larger than the {self._limit // (1024 * 1024)} MB limit",
                )
        return chunk


@dataclass
class ImportResult:
    """What an import produced.

    ``duplicate`` is returned rather than raised: re-importing a paper you already have
    should take you to it, not show an error.  The contract's ``DUPLICATE_DOCUMENT`` code
    is reserved for a caller that explicitly wants a second copy.
    """

    document: Document
    probe: FormatProbe
    duplicate: bool = False
    warnings: list[str] = field(default_factory=list)
    job: Job | None = None
    job_created: bool = False

    @property
    def parse_queued(self) -> bool:
        return self.job is not None


class DocumentService:
    """Import, list, read and delete documents."""

    def __init__(self, settings: Settings, workspace: Workspace, queue: JobQueue) -> None:
        self.settings = settings
        self.workspace = workspace
        self.queue = queue

    # -- import ------------------------------------------------------------

    def import_file(
        self,
        session: Session,
        *,
        filename: str,
        stream: BinaryIO,
        queue_parse: bool = True,
    ) -> ImportResult:
        filename = self._clean_filename(filename)
        document_id = f"doc_{uuid.uuid4().hex[:20]}"

        reader = _SizeLimitedReader(stream, self.settings.max_file_bytes)
        # Raises FileRejected itself if the cap is passed; store_original's finally clause
        # removes the partial temporary file on the way out.
        stored = self.workspace.store_original(document_id, filename, reader)

        try:
            probe = verify(
                stored.absolute,
                filename,
                max_uncompressed_bytes=self.settings.max_uncompressed_bytes,
                max_archive_ratio=self.settings.max_archive_ratio,
            )
            page_count = self._inspect_pdf(probe, stored.absolute)
        except Exception:
            # Nothing is recorded and nothing is kept for a file we refused.
            self._discard(document_id)
            raise

        if probe.format not in {fmt.value for fmt in DocumentFormat}:
            self._discard(document_id)
            raise FileRejected(ErrorCode.UNSUPPORTED_FORMAT, f"unsupported format {probe.format}")

        existing = self._find_by_hash(session, stored.sha256)
        if existing is not None:
            # Content-addressed storage means the bytes are already on disk under the
            # existing document; this second copy is redundant.
            self._discard(document_id)
            was_deleted = existing.deleted_at is not None
            existing.deleted_at = None
            resumed_job = None
            resumed_created = False
            if was_deleted and queue_parse and not existing.active_revision_id:
                resumed_job, resumed_created = self.queue.enqueue(session,job_type=JobType.PARSE.value,
                    document_id=existing.id,request={"document_id":existing.id,"format":existing.format},
                    idempotency_key=f"restore-import:{existing.id}:{uuid.uuid4().hex}")
            return ImportResult(
                document=existing,
                probe=probe,
                duplicate=True,
                job=resumed_job,
                job_created=resumed_created,
                warnings=["已恢复回收站中的原记录。" if was_deleted else f"already imported as “{existing.filename}”"],
            )

        document = Document(
            id=document_id,
            filename=filename,
            format=probe.format,
            mime_type=probe.mime_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            source_path=stored.relative,
            processing_mode=ProcessingMode.LOCAL.value,
            page_count=page_count,
            parse_status="pending",
            translate_status="pending",
            extra={
                "import_warnings": list(probe.warnings),
                "sniffed": probe.details,
                "declared_filename": filename,
            },
        )
        session.add(document)
        session.flush()

        result = ImportResult(document=document, probe=probe, warnings=list(probe.warnings))

        if queue_parse:
            job, created = self.queue.enqueue(
                session,
                job_type=JobType.PARSE.value,
                document_id=document.id,
                request={"document_id": document.id, "format": document.format},
                # Re-importing the same bytes, or double-clicking, must not queue the
                # same parse twice.
                idempotency_key=f"parse:{document.id}:{stored.sha256}",
            )
            result.job = job
            result.job_created = created

        return result

    def _inspect_pdf(self, probe: FormatProbe, path: Path) -> int | None:
        """Refuse unusable PDFs now, and return the page count."""
        if probe.format != DocumentFormat.PDF.value:
            # A single image is one page; a DOCX page count is not known until conversion.
            return 1 if probe.format in {"png", "jpeg"} else None

        try:
            inspection = inspect_pdf(path)
        except PdfCorruptError as exc:
            raise FileRejected(ErrorCode.CORRUPT_FILE, str(exc)) from exc

        if inspection.needs_password:
            raise FileRejected(
                ErrorCode.ENCRYPTED_FILE,
                "this PDF needs a password to open. Remove the password, then import it.",
            )
        if inspection.page_count <= 0:
            raise FileRejected(
                ErrorCode.CORRUPT_FILE, "this PDF reports no pages, so it cannot be read"
            )
        if inspection.page_count > self.settings.max_pages:
            raise FileRejected(
                ErrorCode.TOO_MANY_PAGES,
                f"this PDF has {inspection.page_count} pages, over the "
                f"{self.settings.max_pages} page limit",
            )
        return inspection.page_count

    # -- queries -----------------------------------------------------------

    def list_documents(
        self,
        session: Session,
        *,
        include_deleted: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Document]:
        stmt = select(Document).order_by(Document.imported_at.desc())
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        stmt = stmt.limit(max(1, min(limit, 1000))).offset(max(0, offset))
        return list(session.execute(stmt).scalars().all())

    def get_document(self, session: Session, document_id: str) -> Document:
        document = session.get(Document, document_id)
        if document is None or document.deleted_at is not None:
            raise FileRejected(ErrorCode.NOT_FOUND, f"no document {document_id}")
        return document

    def find_by_hash(self, session: Session, sha256: str) -> Document | None:
        return self._find_by_hash(session, sha256)

    @staticmethod
    def _find_by_hash(session: Session, sha256: str) -> Document | None:
        stmt = (
            select(Document)
            .where(Document.sha256 == sha256)
            .order_by(Document.imported_at.asc())
        )
        return session.execute(stmt).scalars().first()

    def original_path(self, document: Document) -> Path:
        return self.workspace.resolve(document.source_path)

    # -- deletion ----------------------------------------------------------

    def delete_document(self, session: Session, document_id: str) -> dict[str, Any]:
        """Remove a document completely: rows, originals, derived files and exports.

        A partial delete is worse than none — it leaves a row pointing at missing bytes,
        or bytes nothing references.  The files go first, then the rows, and the row
        deletion cascades to pages, blocks, spans, translations, annotations and jobs
        (``ON DELETE CASCADE`` plus ``PRAGMA foreign_keys=ON``).
        """
        document = session.get(Document, document_id)
        if document is None:
            raise FileRejected(ErrorCode.NOT_FOUND, f"no document {document_id}")

        files = self.workspace.delete_document(document_id)
        session.delete(document)
        session.flush()
        return {
            "document_id": document_id,
            "files_removed": files,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        }

    # -- helpers -----------------------------------------------------------

    def _discard(self, document_id: str) -> None:
        """Remove a just-written copy, without masking whatever caused the removal.

        Cleanup failures are swallowed on purpose.  If this raised, it would replace the
        real reason the import was refused — "this PDF needs a password" would surface as
        a Windows sharing violation, and the user would be told the wrong thing.  A
        leftover file in a refused import's directory is harmless; a misleading error is
        not.
        """
        try:
            self.workspace.delete_document(document_id)
        except OSError:
            pass

    @staticmethod
    def _clean_filename(filename: str) -> str:
        """Keep the name as display text, never as a path.

        A browser can send a full path, and a hostile one can send ``..\\..\\x``.  Only the
        final component is kept, and it is stored as a column, not used to build a path —
        storage paths are derived from the document id and content hash instead.
        """
        name = (filename or "").replace("\\", "/").split("/")[-1].strip()
        name = name.replace("\x00", "")
        if not name:
            return "untitled"
        return name[:500]


__all__ = ["DocumentService", "ImportResult"]
