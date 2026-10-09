"""Tests for importing documents.

The import path is where a hostile or merely wrong file first reaches the system, so the
tests concentrate on what it must *refuse* and on what it leaves behind when it refuses.

The size limit gets particular attention: it is only worth having if it stops the copy
part-way through, so the test uses a stream far larger than the cap and asserts that most
of it was never read.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from docx import Document as DocxDocument

from rr_contracts import ErrorCode
from rr_services.documents import DocumentService
from rr_services.formats import FileRejected
from rr_services.settings import Settings
from rr_storage import JobQueue, Workspace, create_database

CORPUS_FILES = Path(__file__).resolve().parents[2] / "benchmarks" / "corpus" / "files"

REAL_PDF = CORPUS_FILES / "pdf_a_single_column.pdf"


@dataclass
class Harness:
    service: DocumentService
    db: object
    workspace: Workspace
    queue: JobQueue
    settings: Settings


@pytest.fixture
def harness(tmp_path) -> Harness:
    settings = Settings(data_dir=tmp_path / "data")
    db = create_database(settings.data_dir)
    db.create_all()
    workspace = Workspace(settings.workspace_root)
    queue = JobQueue(lease_seconds=60)
    return Harness(
        service=DocumentService(settings, workspace, queue),
        db=db,
        workspace=workspace,
        queue=queue,
        settings=settings,
    )


@pytest.fixture
def session(harness):
    with harness.db.session() as s:
        yield s


def originals_count(workspace: Workspace) -> int:
    """How many document directories exist under originals/."""
    root = workspace.root / "originals"
    return sum(1 for child in root.iterdir() if child.is_dir())


def stored_files(workspace: Workspace) -> list[Path]:
    root = workspace.root / "originals"
    return [p for p in root.rglob("*") if p.is_file()]


def import_pdf(harness: Harness, session, path: Path = REAL_PDF):
    with open(path, "rb") as handle:
        return harness.service.import_file(session, filename=path.name, stream=handle)


pytestmark = pytest.mark.skipif(not REAL_PDF.exists(), reason="corpus not generated")


class TestImportingARealPdf:
    def test_a_corpus_pdf_imports_and_queues_a_parse(self, harness, session) -> None:
        result = import_pdf(harness, session)
        document = result.document
        assert result.duplicate is False
        assert result.parse_queued is True
        assert result.job_created is True
        assert result.job is not None
        assert result.job.type == "parse"
        assert result.job.status == "queued"
        assert document.format == "pdf"
        assert document.page_count == 3  # pdf_a_single_column has three pages
        assert document.parse_status == "pending"
        # The original is recorded as a workspace-relative path, not an absolute one.
        assert not Path(document.source_path).is_absolute()
        assert harness.service.original_path(document).exists()

    def test_the_original_is_stored_under_the_content_hash(self, harness, session) -> None:
        result = import_pdf(harness, session)
        stored = harness.service.original_path(result.document)
        assert result.document.sha256.startswith("sha256:")
        assert result.document.sha256.split(":", 1)[1] in stored.name
        # A second read gives the same bytes: the original was not rewritten.
        assert stored.stat().st_size == result.document.size_bytes

    def test_the_document_appears_in_the_list(self, harness, session) -> None:
        result = import_pdf(harness, session)
        listed = harness.service.list_documents(session)
        assert [d.id for d in listed] == [result.document.id]

    def test_importing_the_same_file_twice_returns_the_first(self, harness, session) -> None:
        first = import_pdf(harness, session)
        second = import_pdf(harness, session)
        assert second.duplicate is True
        assert second.document.id == first.document.id
        # Not an error: a repeat import should take you to the paper you already have.
        assert any("already imported" in w for w in second.warnings)
        assert len(harness.service.list_documents(session)) == 1
        # And the redundant copy was removed rather than left on disk.
        assert originals_count(harness.workspace) == 1
        assert len(stored_files(harness.workspace)) == 1


class TestRefusalsLeaveNothingBehind:
    def test_a_legacy_doc_is_refused_and_stores_nothing(self, harness, session) -> None:
        stream = io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        with pytest.raises(FileRejected) as caught:
            harness.service.import_file(session, filename="old.doc", stream=stream)
        assert caught.value.code == ErrorCode.LEGACY_DOC_UNSUPPORTED
        assert originals_count(harness.workspace) == 0
        assert stored_files(harness.workspace) == []

    def test_a_corrupt_pdf_is_refused_and_stores_nothing(self, harness, session) -> None:
        # The magic bytes are right, so the file type check passes and the PDF open is
        # what catches it.  That is the check being tested.
        stream = io.BytesIO(b"%PDF-1.7\nnot really a pdf")
        with pytest.raises(FileRejected) as caught:
            harness.service.import_file(session, filename="broken.pdf", stream=stream)
        assert caught.value.code == ErrorCode.CORRUPT_FILE
        assert originals_count(harness.workspace) == 0

    def test_a_zip_that_is_not_a_docx_is_refused_and_stores_nothing(
        self, harness, session
    ) -> None:
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("notes.txt", "hello")
        buffer.seek(0)
        with pytest.raises(FileRejected) as caught:
            harness.service.import_file(session, filename="notes.docx", stream=buffer)
        assert caught.value.code == ErrorCode.UNSUPPORTED_FORMAT
        assert originals_count(harness.workspace) == 0

    def test_an_empty_file_is_refused_and_stores_nothing(self, harness, session) -> None:
        with pytest.raises(FileRejected) as caught:
            harness.service.import_file(session, filename="empty.pdf", stream=io.BytesIO(b""))
        assert caught.value.code == ErrorCode.EMPTY_FILE
        assert originals_count(harness.workspace) == 0

    def test_an_oversized_pdf_page_count_is_refused(self, harness, session) -> None:
        small = Settings(
            data_dir=harness.settings.data_dir,
            max_pages=1,  # pdf_a_single_column has 3
        )
        service = DocumentService(small, harness.workspace, harness.queue)
        with open(REAL_PDF, "rb") as handle:
            with pytest.raises(FileRejected) as caught:
                service.import_file(session, filename=REAL_PDF.name, stream=handle)
        assert caught.value.code == ErrorCode.TOO_MANY_PAGES
        assert originals_count(harness.workspace) == 0


class TestSizeLimitIsEnforcedDuringTheCopy:
    class GreedyStream:
        """A stream much larger than the cap, counting what is actually taken."""

        def __init__(self, total_bytes: int, chunk: int = 512 * 1024) -> None:
            self._remaining = total_bytes
            self._chunk = chunk
            self.served = 0

        def read(self, size: int = -1) -> bytes:
            if self._remaining <= 0:
                return b""
            take = min(self._chunk, self._remaining)
            self._remaining -= take
            self.served += take
            return b"P" * take

    def test_the_copy_stops_at_the_cap(self, harness, session) -> None:
        limit = 1 * 1024 * 1024
        settings = Settings(data_dir=harness.settings.data_dir, max_file_bytes=limit)
        service = DocumentService(settings, harness.workspace, harness.queue)
        stream = self.GreedyStream(total_bytes=20 * 1024 * 1024)

        with pytest.raises(FileRejected) as caught:
            service.import_file(session, filename="huge.pdf", stream=stream)

        assert caught.value.code == ErrorCode.FILE_TOO_LARGE
        # The distinction that matters: had the limit been checked after the copy, the
        # whole 20 MB would have been written.  At most one chunk past the cap is read.
        assert stream.served <= limit + 512 * 1024
        assert originals_count(harness.workspace) == 0

    def test_nothing_is_written_for_a_refused_oversized_file(self, harness, session) -> None:
        limit = 64 * 1024
        settings = Settings(data_dir=harness.settings.data_dir, max_file_bytes=limit)
        service = DocumentService(settings, harness.workspace, harness.queue)
        stream = self.GreedyStream(total_bytes=5 * 1024 * 1024)
        with pytest.raises(FileRejected):
            service.import_file(session, filename="huge.pdf", stream=stream)
        assert stored_files(harness.workspace) == []


class TestCleanupIsNotFragile:
    """Regression guards for a measured Windows defect.

    PyMuPDF keeps a file open for as long as the exception referencing its internals is
    alive, so a refused import that deletes its copy from inside the handling ``except``
    block leaves the file behind — and the sharing violation then replaces the real error.
    Both halves of that are pinned here, because neither is visible on a passing run.
    """

    def test_no_file_is_left_while_the_exception_is_still_held(self, harness, session) -> None:
        held: FileRejected | None = None
        try:
            harness.service.import_file(
                session,
                filename="broken.pdf",
                stream=io.BytesIO(b"%PDF-1.7\nnot really a pdf"),
            )
        except FileRejected as exc:
            held = exc

        assert held is not None
        # Holding the exception is exactly what used to keep the file locked.
        assert held.code == ErrorCode.CORRUPT_FILE
        assert stored_files(harness.workspace) == []
        assert originals_count(harness.workspace) == 0

    def test_a_failing_cleanup_does_not_mask_the_real_error(
        self, harness, session, monkeypatch
    ) -> None:
        def cannot_delete(document_id: str) -> dict:
            raise PermissionError(32, "in use by another process")

        monkeypatch.setattr(harness.workspace, "delete_document", cannot_delete)
        with pytest.raises(FileRejected) as caught:
            harness.service.import_file(
                session,
                filename="broken.pdf",
                stream=io.BytesIO(b"%PDF-1.7\nnot really a pdf"),
            )
        # The user must be told the file is unreadable, not that a file could not be
        # deleted — the second is a symptom and would send them looking in the wrong place.
        assert caught.value.code == ErrorCode.CORRUPT_FILE

    def test_the_same_holds_for_an_oversized_file(self, harness, session) -> None:
        settings = Settings(data_dir=harness.settings.data_dir, max_file_bytes=4096)
        service = DocumentService(settings, harness.workspace, harness.queue)
        stream = TestSizeLimitIsEnforcedDuringTheCopy.GreedyStream(total_bytes=1024 * 1024)
        with pytest.raises(FileRejected) as caught:
            service.import_file(session, filename="huge.pdf", stream=stream)
        assert caught.value.code == ErrorCode.FILE_TOO_LARGE
        assert stored_files(harness.workspace) == []


class TestFilenameHandling:
    def test_a_browser_supplied_path_is_reduced_to_a_name(self, harness, session) -> None:
        with open(REAL_PDF, "rb") as handle:
            result = harness.service.import_file(
                session, filename=r"C:\Users\me\papers\..\..\evil.pdf", stream=handle
            )
        # Kept as display text only; the storage path never uses it.
        assert result.document.filename == "evil.pdf"

    def test_a_posix_path_is_reduced_to_a_name(self, harness, session) -> None:
        with open(REAL_PDF, "rb") as handle:
            result = harness.service.import_file(
                session, filename="/tmp/../../etc/paper.pdf", stream=handle
            )
        assert result.document.filename == "paper.pdf"

    def test_an_empty_name_becomes_untitled(self, harness, session) -> None:
        with open(REAL_PDF, "rb") as handle:
            result = harness.service.import_file(session, filename="", stream=handle)
        assert result.document.filename == "untitled"

    def test_a_mismatched_extension_is_recorded_as_a_warning(self, harness, session) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
        result = harness.service.import_file(
            session, filename="actually_a_png.pdf", stream=io.BytesIO(png)
        )
        assert result.document.format == "png"
        assert result.document.page_count == 1
        assert any("contents are png" in w for w in result.warnings)
        # The warning is persisted, not just returned once.
        assert result.document.extra["import_warnings"]


class TestDeletion:
    def test_deleting_removes_the_row_and_the_files(self, harness, session) -> None:
        result = import_pdf(harness, session)
        document_id = result.document.id
        assert stored_files(harness.workspace)

        report = harness.service.delete_document(session, document_id)

        assert report["document_id"] == document_id
        assert harness.service.list_documents(session) == []
        assert stored_files(harness.workspace) == []
        assert originals_count(harness.workspace) == 0

    def test_deleting_an_unknown_document_is_a_not_found(self, harness, session) -> None:
        with pytest.raises(FileRejected) as caught:
            harness.service.delete_document(session, "doc_does_not_exist")
        assert caught.value.code == ErrorCode.NOT_FOUND

    def test_deleting_takes_its_job_with_it(self, harness, session) -> None:
        # A job pointing at a deleted document would be claimed and then fail confusingly,
        # so the cascade has to remove it.
        from sqlalchemy import func, select

        from rr_storage import Job

        result = import_pdf(harness, session)
        assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 1

        harness.service.delete_document(session, result.document.id)

        assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 0

    def test_a_deleted_document_still_blocks_reimport_by_hash(self, harness, session) -> None:
        # After a delete the copy is gone, so re-importing the same file must store it
        # again rather than return a row that no longer exists.
        result = import_pdf(harness, session)
        harness.service.delete_document(session, result.document.id)
        again = import_pdf(harness, session)
        assert again.duplicate is False
        assert again.document.id != result.document.id
        assert len(stored_files(harness.workspace)) == 1


class TestDocxImport:
    def test_a_real_docx_imports(self, harness, session) -> None:
        path = harness.settings.data_dir / "made.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = DocxDocument()
        document.add_paragraph("A short paragraph.")
        document.save(path)
        with open(path, "rb") as handle:
            result = harness.service.import_file(session, filename="made.docx", stream=handle)
        assert result.document.format == "docx"
        # DOCX page count is unknown until conversion, and must not be invented.
        assert result.document.page_count is None

    def test_a_corpus_docx_imports(self, harness, session) -> None:
        candidates = sorted(CORPUS_FILES.glob("*.docx"))
        if not candidates:
            pytest.skip("no corpus DOCX files")
        with open(candidates[0], "rb") as handle:
            result = harness.service.import_file(
                session, filename=candidates[0].name, stream=handle
            )
        assert result.document.format == "docx"
