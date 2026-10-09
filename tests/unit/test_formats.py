"""Tests for content-based file verification.

The point of this module is to not believe the filename, so most of these tests hand it a
file whose name disagrees with its bytes.  The archive checks matter most: a DOCX is a ZIP,
so the limits have to hold *before* anything is expanded, and a test proves that by
refusing a bomb while asserting no entry was read.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from docx import Document as DocxDocument

from rr_contracts import ErrorCode
from rr_services.formats import (
    DEFAULT_MAX_ARCHIVE_RATIO,
    DEFAULT_MAX_UNCOMPRESSED_BYTES,
    FileRejected,
    verify,
)

CORPUS_FILES = Path(__file__).resolve().parents[2] / "benchmarks" / "corpus" / "files"


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def make_docx(path: Path, *, body: str = "hello") -> Path:
    document = DocxDocument()
    document.add_paragraph(body)
    document.save(path)
    return path


def make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class TestAcceptedFormats:
    def test_pdf_is_recognised(self, tmp_path) -> None:
        path = write(tmp_path / "a.pdf", b"%PDF-1.7\nrest of it")
        probe = verify(path, "a.pdf")
        assert probe.format == "pdf"
        assert probe.mime_type == "application/pdf"
        assert probe.extension_matches
        assert probe.warnings == []

    def test_png_is_recognised(self, tmp_path) -> None:
        path = write(tmp_path / "a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        assert verify(path, "a.png").format == "png"

    def test_jpeg_is_recognised(self, tmp_path) -> None:
        path = write(tmp_path / "a.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        assert verify(path, "a.jpg").format == "jpeg"

    def test_a_jpeg_named_jpeg_is_recognised(self, tmp_path) -> None:
        # Both .jpg and .jpeg are in use, and neither should be treated as a mismatch.
        path = write(tmp_path / "a.jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        probe = verify(path, "a.jpeg")
        assert probe.format == "jpeg"
        assert probe.extension_matches

    def test_a_real_docx_is_recognised(self, tmp_path) -> None:
        path = make_docx(tmp_path / "a.docx")
        probe = verify(path, "a.docx")
        assert probe.format == "docx"
        assert probe.extension_matches
        assert probe.details["entries"] > 0


class TestNameDoesNotDecide:
    def test_a_png_named_pdf_is_read_as_a_png_with_a_warning(self, tmp_path) -> None:
        path = write(tmp_path / "lie.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        probe = verify(path, "lie.pdf")
        # The content wins, and the disagreement is reported rather than hidden.
        assert probe.format == "png"
        assert not probe.extension_matches
        assert len(probe.warnings) == 1
        assert "contents are png" in probe.warnings[0]

    def test_a_pdf_named_txt_is_still_accepted(self, tmp_path) -> None:
        # An extension we do not map implies nothing, so there is no mismatch to report.
        path = write(tmp_path / "paper.txt", b"%PDF-1.7\n")
        probe = verify(path, "paper.txt")
        assert probe.format == "pdf"
        assert probe.extension_matches
        assert probe.warnings == []

    def test_a_docx_named_pdf_is_read_as_a_docx(self, tmp_path) -> None:
        path = make_docx(tmp_path / "really.docx")
        renamed = tmp_path / "really.pdf"
        path.rename(renamed)
        probe = verify(renamed, "really.pdf")
        assert probe.format == "docx"
        assert not probe.extension_matches


class TestRejections:
    def test_an_empty_file_is_refused(self, tmp_path) -> None:
        path = write(tmp_path / "empty.pdf", b"")
        with pytest.raises(FileRejected) as caught:
            verify(path, "empty.pdf")
        assert caught.value.code == ErrorCode.EMPTY_FILE

    def test_a_legacy_doc_is_refused_with_a_usable_message(self, tmp_path) -> None:
        path = write(tmp_path / "old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20)
        with pytest.raises(FileRejected) as caught:
            verify(path, "old.doc")
        assert caught.value.code == ErrorCode.LEGACY_DOC_UNSUPPORTED
        # The message must say what to do, not just what failed.
        assert ".docx" in caught.value.message

    def test_an_unknown_format_is_refused_and_names_what_it_saw(self, tmp_path) -> None:
        path = write(tmp_path / "mystery.pdf", b"GIF89a" + b"\x00" * 20)
        with pytest.raises(FileRejected) as caught:
            verify(path, "mystery.pdf")
        assert caught.value.code == ErrorCode.UNSUPPORTED_FORMAT
        assert "47 49 46" in caught.value.message  # "GIF" in hex

    def test_a_plain_zip_is_refused(self, tmp_path) -> None:
        path = make_zip(tmp_path / "archive.docx", {"notes.txt": b"hello"})
        with pytest.raises(FileRejected) as caught:
            verify(path, "archive.docx")
        assert caught.value.code == ErrorCode.UNSUPPORTED_FORMAT
        assert "ZIP archive" in caught.value.message

    def test_another_office_file_is_refused_by_name(self, tmp_path) -> None:
        # An xlsx renamed to .docx: the fix is "wrong file", not "wrong format", so the
        # message must say which it is.
        path = make_zip(
            tmp_path / "sheet.docx",
            {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"},
        )
        with pytest.raises(FileRejected) as caught:
            verify(path, "sheet.docx")
        assert caught.value.code == ErrorCode.UNSUPPORTED_FORMAT
        assert "not a Word document" in caught.value.message

    def test_a_damaged_zip_is_reported_as_corrupt(self, tmp_path) -> None:
        path = write(tmp_path / "broken.docx", b"PK\x03\x04" + b"\x00" * 40)
        with pytest.raises(FileRejected) as caught:
            verify(path, "broken.docx")
        assert caught.value.code == ErrorCode.CORRUPT_FILE


class TestArchiveLimits:
    def test_a_docx_over_the_uncompressed_limit_is_refused(self, tmp_path) -> None:
        path = make_zip(tmp_path / "big.docx", {"word/document.xml": b"0" * 50_000})
        with pytest.raises(FileRejected) as caught:
            verify(path, "big.docx", max_uncompressed_bytes=1_000)
        assert caught.value.code == ErrorCode.DECOMPRESSION_BOMB
        assert "over the" in caught.value.message

    def test_an_expansion_bomb_is_refused(self, tmp_path) -> None:
        # Zeros compress to almost nothing, so this is the shape of a real bomb: tiny on
        # the wire, large when expanded.
        path = make_zip(tmp_path / "bomb.docx", {"word/document.xml": b"\x00" * 200_000})
        with pytest.raises(FileRejected) as caught:
            verify(path, "bomb.docx", max_archive_ratio=5.0)
        assert caught.value.code == ErrorCode.DECOMPRESSION_BOMB
        assert "x" in caught.value.message

    def test_the_bomb_is_refused_without_expanding_any_entry(self, tmp_path, monkeypatch) -> None:
        # If the guard ran after decompression it would already have paid the cost it
        # exists to avoid, so fail the test if any entry is actually read.
        path = make_zip(tmp_path / "bomb.docx", {"word/document.xml": b"\x00" * 200_000})

        def explode(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the archive was decompressed before the limit was checked")

        monkeypatch.setattr(zipfile.ZipExtFile, "read", explode)
        with pytest.raises(FileRejected) as caught:
            verify(path, "bomb.docx", max_uncompressed_bytes=1_000)
        assert caught.value.code == ErrorCode.DECOMPRESSION_BOMB

    def test_a_normal_docx_passes_the_default_limits(self, tmp_path) -> None:
        path = make_docx(tmp_path / "normal.docx", body="a sentence " * 50)
        probe = verify(path, "normal.docx")
        assert probe.details["uncompressed_bytes"] < DEFAULT_MAX_UNCOMPRESSED_BYTES
        assert probe.details["expansion_ratio"] < DEFAULT_MAX_ARCHIVE_RATIO


class TestAgainstTheRealCorpus:
    """The corpus is the only real PDFs available, so at least one must pass."""

    @pytest.mark.skipif(not CORPUS_FILES.exists(), reason="corpus not generated")
    def test_a_corpus_pdf_is_accepted(self) -> None:
        candidates = sorted(CORPUS_FILES.glob("*.pdf"))
        assert candidates, "no corpus PDFs found"
        for path in candidates[:6]:
            probe = verify(path, path.name)
            assert probe.format == "pdf", path.name
            assert probe.extension_matches, path.name

    @pytest.mark.skipif(not CORPUS_FILES.exists(), reason="corpus not generated")
    def test_a_corpus_docx_is_accepted(self) -> None:
        candidates = sorted(CORPUS_FILES.glob("*.docx"))
        if not candidates:
            pytest.skip("no corpus DOCX files")
        for path in candidates[:4]:
            probe = verify(path, path.name)
            assert probe.format == "docx", path.name
