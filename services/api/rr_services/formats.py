"""File type verification by content, not by extension.

A file's name is a claim; its bytes are the evidence.  The spec requires real type checks
before anything is parsed, so this module reads magic bytes and, for the ZIP-based formats,
the archive's central directory — never the extension alone.

It also owns the size and archive limits, and it refuses *before* decompressing: a DOCX
whose central directory declares more uncompressed data than the cap allows is rejected
without inflating a single byte.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from rr_contracts import ErrorCode

#: First bytes of each format we accept.  Longest-first matching matters: JPEG's is a
#: prefix of nothing else here, but PDF's is checked before the ZIP family for clarity.
MAGIC_PDF = b"%PDF-"
MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
MAGIC_JPEG = b"\xff\xd8\xff"
MAGIC_ZIP = b"PK\x03\x04"
#: OLE2 compound file: legacy .doc, .xls, .ppt.  Detected only so the error can name it.
MAGIC_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

#: Extension -> the format that extension implies.  Used only to notice a mismatch.
FORMAT_BY_EXTENSION = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".docx": "docx",
}

#: The part of a ZIP that makes it a Word document rather than any other OOXML file.
DOCX_PART = "word/document.xml"
CONTENT_TYPES_PART = "[Content_Types].xml"

#: Archive limits.  A DOCX is a ZIP, so an upload can be a few kilobytes on the wire and
#: gigabytes when expanded.  Both limits must pass or the file is refused.
DEFAULT_MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_RATIO = 200.0


class FileRejected(Exception):
    """A file that cannot be accepted, carrying the shared contract's error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FormatProbe:
    """What the bytes turned out to be, and what was suspicious about them."""

    format: str
    mime_type: str
    extension_format: str | None
    warnings: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    @property
    def extension_matches(self) -> bool:
        """True when the name agrees with the content, or the name implies nothing."""
        return self.extension_format is None or self.extension_format == self.format


def _read_head(path: Path, size: int = 8) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(size)
    except OSError as exc:  # pragma: no cover - unreadable file is an environment fault
        raise FileRejected(ErrorCode.CORRUPT_FILE, f"cannot read file: {exc}") from exc


def _inspect_docx(
    path: Path,
    *,
    max_uncompressed_bytes: int,
    max_archive_ratio: float,
) -> dict:
    """Check a ZIP really is a Word document and is not a decompression bomb.

    Reads only the central directory, which is metadata: no entry is inflated, so a bomb
    is refused without the cost of expanding it.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise FileRejected(
            ErrorCode.CORRUPT_FILE, f"the DOCX archive is damaged: {exc}"
        ) from exc

    names = {info.filename for info in infos}
    if DOCX_PART not in names:
        # Distinguish "some other Office file" from "a plain archive", because the fix
        # is different for each.
        if CONTENT_TYPES_PART in names:
            raise FileRejected(
                ErrorCode.UNSUPPORTED_FORMAT,
                "this is an Office file but not a Word document "
                "(it has no word/document.xml)",
            )
        raise FileRejected(
            ErrorCode.UNSUPPORTED_FORMAT,
            "this is a ZIP archive, not a Word document",
        )

    uncompressed = sum(info.file_size for info in infos)
    compressed = sum(info.compress_size for info in infos) or 1
    ratio = uncompressed / compressed

    if uncompressed > max_uncompressed_bytes:
        raise FileRejected(
            ErrorCode.DECOMPRESSION_BOMB,
            f"the DOCX expands to {uncompressed} bytes, over the "
            f"{max_uncompressed_bytes} byte limit",
        )
    if ratio > max_archive_ratio:
        raise FileRejected(
            ErrorCode.DECOMPRESSION_BOMB,
            f"the DOCX expands {ratio:.0f}x, over the {max_archive_ratio:.0f}x limit",
        )

    return {
        "entries": len(infos),
        "uncompressed_bytes": uncompressed,
        "compressed_bytes": compressed,
        "expansion_ratio": round(ratio, 2),
    }


def verify(
    path: str | Path,
    declared_filename: str,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_archive_ratio: float = DEFAULT_MAX_ARCHIVE_RATIO,
) -> FormatProbe:
    """Decide what ``path`` actually is, or raise :class:`FileRejected`.

    The content decides; the name is only checked for agreement, and a disagreement is a
    warning rather than a rejection — a PDF named ``.png`` is still readable, and
    refusing it would be obstructive.  Trusting the *name* over the content is the
    mistake this function exists to prevent.
    """
    path = Path(path)
    size = path.stat().st_size
    if size == 0:
        raise FileRejected(ErrorCode.EMPTY_FILE, "the file is empty")

    head = _read_head(path, 16)
    extension = Path(declared_filename).suffix.lower()
    extension_format = FORMAT_BY_EXTENSION.get(extension)

    if head.startswith(MAGIC_PDF):
        probe = FormatProbe("pdf", MIME_BY_FORMAT["pdf"], extension_format, details={"size_bytes": size})
    elif head.startswith(MAGIC_PNG):
        probe = FormatProbe("png", MIME_BY_FORMAT["png"], extension_format, details={"size_bytes": size})
    elif head.startswith(MAGIC_JPEG):
        probe = FormatProbe("jpeg", MIME_BY_FORMAT["jpeg"], extension_format, details={"size_bytes": size})
    elif head.startswith(MAGIC_OLE2):
        raise FileRejected(
            ErrorCode.LEGACY_DOC_UNSUPPORTED,
            "this is a legacy Word document (.doc). Open it in Word and save it as "
            ".docx, then import that.",
        )
    elif head.startswith(MAGIC_ZIP):
        details = _inspect_docx(
            path,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_archive_ratio=max_archive_ratio,
        )
        details["size_bytes"] = size
        probe = FormatProbe("docx", MIME_BY_FORMAT["docx"], extension_format, details=details)
    else:
        raise FileRejected(
            ErrorCode.UNSUPPORTED_FORMAT,
            "the file is not a PDF, PNG, JPEG or DOCX "
            f"(it starts with {head[:8].hex(' ')})",
        )

    if not probe.extension_matches:
        probe.warnings.append(
            f"the file is named {declared_filename} but its contents are "
            f"{probe.format}; the contents were used"
        )
    return probe


__all__ = [
    "CONTENT_TYPES_PART",
    "DEFAULT_MAX_ARCHIVE_RATIO",
    "DEFAULT_MAX_UNCOMPRESSED_BYTES",
    "DOCX_PART",
    "FORMAT_BY_EXTENSION",
    "FileRejected",
    "FormatProbe",
    "MAGIC_JPEG",
    "MAGIC_OLE2",
    "MAGIC_PDF",
    "MAGIC_PNG",
    "MAGIC_ZIP",
    "MIME_BY_FORMAT",
    "verify",
]
