"""Workspace layout: immutable originals, derived resources, exports.

Layout under the storage root::

    data/
      originals/<document_id>/<sha256>.<ext>   # never modified after import
      derived/<document_id>/<revision_id>/...  # previews, thumbnails, OCR crops
      exports/<document_id>/<job_id>/...       # Markdown resource bundles
      tmp/                                     # staging for atomic writes

Spec 11 requires the original to be immutable, so imports are streamed to a
temporary file, verified by hash, and then moved into place.  Deleting a
document removes every directory, which is what makes the "delete covers
originals, derived resources and caches" acceptance item checkable.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Workspace", "StoredFile"]


@dataclass(frozen=True)
class StoredFile:
    absolute: Path
    relative: str
    sha256: str
    size_bytes: int


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        for sub in ("originals", "derived", "exports", "tmp"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
    def original_dir(self, document_id: str) -> Path:
        return self.root / "originals" / document_id

    def derived_dir(self, document_id: str, revision_id: str | None = None) -> Path:
        base = self.root / "derived" / document_id
        return base / revision_id if revision_id else base

    def export_dir(self, document_id: str, job_id: str) -> Path:
        return self.root / "exports" / document_id / job_id

    def tmp_path(self, name: str) -> Path:
        safe = name.replace("/", "_").replace("\\", "_")
        return self.root / "tmp" / safe

    def resolve(self, relative: str) -> Path:
        """Resolve a stored relative path, refusing to escape the root."""
        candidate = (self.root / relative).resolve()
        if not str(candidate).startswith(str(self.root)):
            raise ValueError(f"path escapes the workspace root: {relative}")
        return candidate

    def relative(self, absolute: Path) -> str:
        return str(Path(absolute).resolve().relative_to(self.root)).replace("\\", "/")

    # -- ingest ------------------------------------------------------------
    def store_original(self, document_id: str, filename: str, stream) -> StoredFile:
        """Stream an upload into the originals tree, hashing as it goes."""
        extension = Path(filename).suffix.lower() or ".bin"
        tmp = self.tmp_path(f"{document_id}{extension}")
        digest = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as handle:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
            target_dir = self.original_dir(document_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{digest.hexdigest()}{extension}"
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return StoredFile(
            absolute=target,
            relative=self.relative(target),
            sha256="sha256:" + digest.hexdigest(),
            size_bytes=size,
        )

    def write_bytes(self, relative: str, data: bytes, *, atomic: bool = True) -> StoredFile:
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        if atomic:
            tmp = self.tmp_path(target.name)
            tmp.write_bytes(data)
            os.replace(tmp, target)
        else:
            target.write_bytes(data)
        return StoredFile(
            absolute=target,
            relative=self.relative(target),
            sha256="sha256:" + digest,
            size_bytes=len(data),
        )

    def new_derived_dir(self, document_id: str, revision_id: str) -> Path:
        path = self.derived_dir(document_id, revision_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- delete ------------------------------------------------------------
    def delete_document(self, document_id: str) -> dict[str, int]:
        """Remove all files for a document.  Returns a per-area file count."""
        removed = {"originals": 0, "derived": 0, "exports": 0}
        for key, path in (
            ("originals", self.root / "originals" / document_id),
            ("derived", self.root / "derived" / document_id),
            ("exports", self.root / "exports" / document_id),
        ):
            if path.exists():
                removed[key] = sum(1 for p in path.rglob("*") if p.is_file())
                shutil.rmtree(path, ignore_errors=False)
        return removed

    def disk_usage(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sub in ("originals", "derived", "exports"):
            base = self.root / sub
            out[sub] = sum(p.stat().st_size for p in base.rglob("*") if p.is_file())
        return out
