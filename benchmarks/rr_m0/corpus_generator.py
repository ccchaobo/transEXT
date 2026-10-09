"""Generate the labelled acceptance corpus.

Run::

    python -m rr_m0.corpus_generator --root benchmarks/corpus

Produces, under the given root:

* ``files/``  -- the documents themselves (PDF / PNG / JPEG / DOCX)
* ``images/`` -- standalone page images for the OCR suite
* ``truth/``  -- per-document ground truth recorded at draw time
* ``manifest.json`` -- hashes, sizes, layout class and ground-truth pointers
* ``anomalies/`` -- deliberately broken inputs for the error-path suite

The corpus is generated, not downloaded: every document is authored in
:mod:`rr_m0.corpus_content`, so the ground truth is exact and the files are
freely redistributable.  See :mod:`rr_m0.corpus_layout` for why that matters and
what it does not prove.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import pymupdf
from PIL import Image, ImageDraw, ImageFilter

from . import corpus_content as content
from .corpus_docx import DocxBuilder
from .corpus_layout import (
    PAGE_H,
    PAGE_W,
    BlockSpec,
    ContentItem,
    DocumentSpec,
    LayoutKind,
    LineSpec,
    PageSpec,
    TextPdfBuilder,
)

__all__ = ["CorpusGenerator", "main"]


# ---------------------------------------------------------------------------
# Degradation profiles for the scanned subset
# ---------------------------------------------------------------------------


@dataclass
class ScanProfile:
    """How a rendered page is degraded to imitate a scan.

    The parameters are chosen to model real capture conditions rather than to be
    maximally harsh:

    * ``jpeg_quality`` and ``blur_radius`` model the imaging chain of a flatbed
      scanner, which is mild.  A clean 300 dpi scan of printed text is legible,
      and a profile that makes it illegible would measure the profile instead of
      the recogniser.
    * ``noise_sigma`` is in 0-255 intensity units.  Real sensor noise on a white
      background is roughly 1-3; values above ~10 model a poor photograph.
    * ``speckles`` models dust and toner spots.  A few hundred are realistic for
      a used page; thousands model a dirty drum.

    Each profile's parameters are recorded in the document ground truth and in
    the manifest, so a measurement can always be attributed to a described image.
    """

    name: str
    dpi: int = 300
    rotation_deg: float = 0.0
    noise_sigma: float = 2.0
    blur_radius: float = 0.3
    jpeg_quality: int = 88
    contrast: float = 1.0
    brightness: int = 0
    vignette: bool = False
    speckles: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "dpi": self.dpi,
            "rotation_deg": self.rotation_deg,
            "noise_sigma": self.noise_sigma,
            "blur_radius": self.blur_radius,
            "jpeg_quality": self.jpeg_quality,
            "contrast": self.contrast,
            "brightness": self.brightness,
            "vignette": self.vignette,
            "speckles": self.speckles,
        }


#: A good flatbed scan: light sensor noise, mild optical softening, high-quality
#: JPEG.  This is the profile the clear-scan acceptance figures are measured on.
CLEAN_SCAN = ScanProfile("clean_300dpi", dpi=300, noise_sigma=1.6, blur_radius=0.3, jpeg_quality=90)
CLEAN_SCAN_LOW = ScanProfile("clean_200dpi", dpi=200, noise_sigma=2.0, blur_radius=0.4, jpeg_quality=86)
#: A degraded scan: visible grain, toner spots, aggressive compression.  Its
#: figures are reported separately and are never merged with the clean ones.
NOISY_SCAN = ScanProfile(
    "noisy_300dpi", dpi=300, noise_sigma=9.0, blur_radius=0.6, jpeg_quality=62,
    contrast=0.92, speckles=260,
)
SKEWED_SCAN = ScanProfile(
    "skewed_300dpi", dpi=300, rotation_deg=1.8, noise_sigma=4.0, blur_radius=0.5,
    jpeg_quality=76, speckles=120,
)
PHOTO_SCAN = ScanProfile(
    "photo_like", dpi=240, rotation_deg=-2.4, noise_sigma=11.0, blur_radius=0.9,
    jpeg_quality=58, contrast=0.88, brightness=-6, vignette=True, speckles=400,
)
GRAYSCALE_SCAN = ScanProfile(
    "grayscale_300dpi", dpi=300, noise_sigma=3.0, blur_radius=0.4, jpeg_quality=82, contrast=1.05
)


@dataclass
class CorpusEntry:
    """One generated file plus how to check it."""

    name: str
    kind: str            # text_pdf | scanned_pdf | mixed_pdf | image | docx | anomaly
    layout: str
    path: str
    truth_path: str | None
    page_count: int
    notes: str = ""
    profile: dict[str, Any] | None = None
    expected_error: str | None = None
    sha256: str = ""
    size_bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "layout": self.layout,
            "path": self.path,
            "truth_path": self.truth_path,
            "page_count": self.page_count,
            "notes": self.notes,
            "profile": self.profile,
            "expected_error": self.expected_error,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            **({"extra": self.extra} if self.extra else {}),
        }


class CorpusGenerator:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.truth_dir = self.root / "truth"
        self.images_dir = self.root / "images"
        self.anomalies_dir = self.root / "anomalies"
        for directory in (self.files_dir, self.truth_dir, self.images_dir, self.anomalies_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.entries: list[CorpusEntry] = []
        self._rng = random.Random(20261009)

    # -- helpers -----------------------------------------------------------
    def _record(self, path: Path) -> tuple[str, int]:
        data = path.read_bytes()
        return "sha256:" + hashlib.sha256(data).hexdigest(), len(data)

    def _write_truth(self, name: str, payload: dict) -> Path:
        target = self.truth_dir / f"{name}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return target

    def _entry(
        self,
        *,
        name: str,
        kind: str,
        layout: str,
        path: Path,
        truth_path: Path | None,
        page_count: int,
        notes: str = "",
        profile: ScanProfile | None = None,
        expected_error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CorpusEntry:
        sha, size = self._record(path)
        entry = CorpusEntry(
            name=name,
            kind=kind,
            layout=layout,
            path=str(path.relative_to(self.root)).replace("\\", "/"),
            truth_path=str(truth_path.relative_to(self.root)).replace("\\", "/") if truth_path else None,
            page_count=page_count,
            notes=notes,
            profile=profile.describe() if profile else None,
            expected_error=expected_error,
            sha256=sha,
            size_bytes=size,
            extra=extra or {},
        )
        self.entries.append(entry)
        return entry

    # -- text PDF ----------------------------------------------------------
    def build_text_pdf(
        self,
        name: str,
        title: str,
        authors: str,
        columns: int,
        items: Sequence[ContentItem],
        *,
        layout: LayoutKind | None = None,
        notes: str = "",
        page_rotations: dict[int, int] | None = None,
        page_labels: dict[int, str] | None = None,
        extra_truth: dict[str, Any] | None = None,
    ) -> tuple[DocumentSpec, Path]:
        layout = layout or (LayoutKind.DOUBLE if columns == 2 else LayoutKind.SINGLE)
        spec = DocumentSpec(
            name=name,
            title=title,
            authors=authors,
            layout=layout,
            columns=columns,
            notes=notes,
            has_references=True,
            cross_page_paragraph=False,
        )
        target = self.files_dir / f"{name}.pdf"
        builder = TextPdfBuilder(spec, target)
        builder.build(items)

        rotations = page_rotations or {}
        labels = page_labels or {}
        for page in spec.pages:
            page.rotation = rotations.get(page.index, 0)
            if page.index in labels:
                page.printed_label = labels[page.index]
                page.footer_text = labels[page.index]

        truth = spec.to_dict()
        truth["source_format"] = "pdf"
        truth["source_kind"] = "text"
        truth["name"] = name
        if extra_truth:
            truth.update(extra_truth)
        truth_path = self._write_truth(name, truth)
        self._entry(
            name=name,
            kind="text_pdf",
            layout=layout.value,
            path=target,
            truth_path=truth_path,
            page_count=len(spec.pages),
            notes=notes,
            extra={"has_formula": spec.has_formula, "has_table": spec.has_table, "has_figure": spec.has_figure},
        )
        return spec, target

    # -- scanned PDF -------------------------------------------------------
    def build_scanned_pdf(
        self,
        name: str,
        source_pdf: Path,
        source_spec: DocumentSpec,
        profile: ScanProfile,
        *,
        notes: str = "",
        mixed_scan_pages: Sequence[int] | None = None,
        image_format: str = "jpeg",
    ) -> Path:
        """Render ``source_pdf`` through ``profile`` into a new PDF.

        The ground truth of the source document is reused, but each page records
        the degradation it received and any rotation the *image* carries, so a
        coordinate check can account for it.  ``mixed_scan_pages`` rebuilds a PDF
        in which only those pages are rasterised and the rest keep their text
        layer, producing a genuinely hybrid document.
        """
        source = pymupdf.open(str(source_pdf))
        scan_pages = set(mixed_scan_pages) if mixed_scan_pages is not None else set(range(source.page_count))
        output = pymupdf.open()
        page_truth: list[dict[str, Any]] = []
        try:
            for page_index in range(source.page_count):
                src_page = source.load_page(page_index)
                page_rotation = 0
                for page in source_spec.pages:
                    if page.index == page_index:
                        page_rotation = page.rotation
                if page_index in scan_pages:
                    pixmap = src_page.get_pixmap(dpi=profile.dpi, alpha=False)
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    image = self._degrade(image, profile)
                    if page_rotation:
                        # PIL rotates counter-clockwise, PDF /Rotate is clockwise.
                        image = image.rotate(-page_rotation, expand=True, fillcolor=(255, 255, 255))
                    buffer = _encode(image, image_format, profile.jpeg_quality)
                    rect = pymupdf.Rect(0, 0, image.width * 72.0 / profile.dpi, image.height * 72.0 / profile.dpi)
                    new_page = output.new_page(width=rect.width, height=rect.height)
                    new_page.insert_image(rect, stream=buffer)
                    page_truth.append(
                        {
                            "index": page_index,
                            "rendered": True,
                            "rotation": page_rotation,
                            "dpi": profile.dpi,
                            "scan_rotation_deg": profile.rotation_deg,
                            "width_pt": rect.width,
                            "height_pt": rect.height,
                        }
                    )
                else:
                    new_page = output.new_page(width=src_page.rect.width, height=src_page.rect.height)
                    new_page.show_pdf_page(new_page.rect, source, page_index)
                    page_truth.append(
                        {
                            "index": page_index,
                            "rendered": False,
                            "rotation": page_rotation,
                            "dpi": 72.0,
                            "width_pt": src_page.rect.width,
                            "height_pt": src_page.rect.height,
                        }
                    )
            target = self.files_dir / f"{name}.pdf"
            output.save(str(target), deflate=True, garbage=3)
        finally:
            output.close()
            source.close()

        truth = source_spec.to_dict()
        truth["source_format"] = "pdf"
        truth["source_kind"] = "mixed" if mixed_scan_pages is not None else "scanned"
        truth["name"] = name
        truth["scan_profile"] = profile.describe()
        truth["scan_pages"] = sorted(scan_pages)
        truth["rendered_pages"] = page_truth
        truth_path = self._write_truth(name, truth)
        kind = "mixed_pdf" if mixed_scan_pages is not None else "scanned_pdf"
        self._entry(
            name=name,
            kind=kind,
            layout=source_spec.layout.value,
            path=target,
            truth_path=truth_path,
            page_count=len(source_spec.pages),
            notes=notes,
            profile=profile,
            extra={"image_format": image_format, "scanned_pages": len(scan_pages)},
        )
        return target

    # -- standalone images -------------------------------------------------
    def build_image(
        self,
        name: str,
        source_pdf: Path,
        source_spec: DocumentSpec,
        profile: ScanProfile,
        page_index: int = 0,
        *,
        image_format: str = "png",
        notes: str = "",
        crop_fraction: float | None = None,
    ) -> Path:
        source = pymupdf.open(str(source_pdf))
        try:
            page = source.load_page(page_index)
            pixmap = page.get_pixmap(dpi=profile.dpi, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            source.close()
        image = self._degrade(image, profile)
        if crop_fraction and 0 < crop_fraction < 1:
            width, height = image.size
            image = image.crop((0, 0, width, int(height * crop_fraction)))
        suffix = "png" if image_format == "png" else "jpg"
        target = self.images_dir / f"{name}.{suffix}"
        buffer = _encode(image, image_format, profile.jpeg_quality)
        target.write_bytes(buffer)

        truth = source_spec.to_dict()
        truth["source_format"] = image_format
        truth["source_kind"] = "image"
        truth["name"] = name
        truth["source_page_index"] = page_index
        truth["scan_profile"] = profile.describe()
        truth["image_size_px"] = list(image.size)
        truth["cropped"] = bool(crop_fraction)
        truth_path = self._write_truth(name, truth)
        self._entry(
            name=name,
            kind="image",
            layout=source_spec.layout.value,
            path=target,
            truth_path=truth_path,
            page_count=1,
            notes=notes,
            profile=profile,
            extra={"page_index": page_index, "image_format": image_format, "image_size_px": list(image.size)},
        )
        return target

    # -- DOCX --------------------------------------------------------------
    def build_docx(
        self,
        name: str,
        title: str,
        authors: str,
        items: Sequence[ContentItem],
        *,
        layout: str = "flow",
        notes: str = "",
    ) -> Path:
        target = self.files_dir / f"{name}.docx"
        builder = DocxBuilder(title, authors, target)
        result = builder.build(items)
        truth = {
            "name": name,
            "source_format": "docx",
            "source_kind": "docx",
            "title": title,
            "authors": authors,
            "layout": layout,
            "text_blocks": result.text_blocks,
            "paragraph_count": result.paragraph_count,
            "table_count": result.table_count,
            "image_count": result.image_count,
            "formula_count": result.formula_count,
            "footnote_count": result.footnote_count,
            "notes": notes,
        }
        truth_path = self._write_truth(name, truth)
        self._entry(
            name=name,
            kind="docx",
            layout=layout,
            path=target,
            truth_path=truth_path,
            page_count=0,  # DOCX has no fixed pagination (spec 5.4)
            notes=notes,
            extra={
                "paragraph_count": result.paragraph_count,
                "table_count": result.table_count,
                "image_count": result.image_count,
                "formula_count": result.formula_count,
                "footnote_count": result.footnote_count,
            },
        )
        return target

    # -- degradation -------------------------------------------------------
    def _degrade(self, image: Image.Image, profile: ScanProfile) -> Image.Image:
        out = image.convert("RGB")
        if profile.rotation_deg:
            out = out.rotate(
                profile.rotation_deg, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255)
            )
        if profile.blur_radius:
            out = out.filter(ImageFilter.GaussianBlur(profile.blur_radius))
        if profile.vignette:
            out = _apply_vignette(out)
        if profile.contrast != 1.0 or profile.brightness:
            from PIL import ImageEnhance

            out = ImageEnhance.Contrast(out).enhance(profile.contrast)
            if profile.brightness:
                from PIL import ImageEnhance as _IE

                out = _IE.Brightness(out).enhance(1.0 + profile.brightness / 100.0)
        if profile.noise_sigma > 0 or profile.speckles:
            out = _add_noise(out, profile.noise_sigma, profile.speckles, self._rng)
        return out

    # -- anomalies ---------------------------------------------------------
    def build_anomalies(self, base_pdf: Path) -> None:
        """Create the error-path inputs from spec 12.1."""
        # 1. Not really a PDF.
        fake = self.anomalies_dir / "corrupt_truncated.pdf"
        data = base_pdf.read_bytes()
        fake.write_bytes(data[: len(data) // 3])
        self._entry(
            name="corrupt_truncated",
            kind="anomaly",
            layout="n/a",
            path=fake,
            truth_path=None,
            page_count=0,
            notes="Truncated PDF: header present, body cut off.",
            expected_error="corrupt_file",
        )

        # 2. Encrypted PDF with an owner/user password.
        encrypted = self.anomalies_dir / "encrypted.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((72, 100), "Encrypted sample: content is not readable without the password.")
        doc.save(
            str(encrypted),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="user-secret",
            owner_pw="owner-secret",
            permissions=0,
        )
        doc.close()
        self._entry(
            name="encrypted",
            kind="anomaly",
            layout="n/a",
            path=encrypted,
            truth_path=None,
            page_count=1,
            notes="AES-256 encrypted PDF; must be reported, never silently retried.",
            expected_error="encrypted_file",
        )

        # 3. Legacy binary .doc (not a renamed DOCX).
        legacy = self.anomalies_dir / "legacy_word97.doc"
        legacy.write_bytes(_legacy_doc_bytes())
        self._entry(
            name="legacy_word97",
            kind="anomaly",
            layout="n/a",
            path=legacy,
            truth_path=None,
            page_count=0,
            notes="Word 97 binary format. Must be rejected with a conversion hint, not parsed as DOCX.",
            expected_error="legacy_doc_unsupported",
        )

        # 4. Empty file.
        empty = self.anomalies_dir / "empty.pdf"
        empty.write_bytes(b"")
        self._entry(
            name="empty",
            kind="anomaly",
            layout="n/a",
            path=empty,
            truth_path=None,
            page_count=0,
            notes="Zero-byte upload.",
            expected_error="empty_file",
        )

        # 5. PDF whose text layer is unusable: only replacement glyphs.
        garbage = self.anomalies_dir / "garbage_text_layer.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((72, 120), "\ufffd" * 60, fontsize=11)
        page.insert_text((72, 140), "\u25a1\u25a1\u25a1 \u2591\u2592\u2593 " * 8, fontsize=11)
        doc.save(str(garbage))
        doc.close()
        self._entry(
            name="garbage_text_layer",
            kind="anomaly",
            layout="n/a",
            path=garbage,
            truth_path=None,
            page_count=1,
            notes="Has a text layer that is present but unusable; must be routed to OCR, not trusted.",
            expected_error=None,
        )

        # 6. Blank page document (no text, no images).
        blank = self.anomalies_dir / "blank_pages.pdf"
        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page(width=PAGE_W, height=PAGE_H)
        doc.save(str(blank))
        doc.close()
        self._entry(
            name="blank_pages",
            kind="anomaly",
            layout="n/a",
            path=blank,
            truth_path=None,
            page_count=3,
            notes="Three genuinely blank pages: must parse to zero blocks without error.",
            expected_error=None,
        )

        # 7. Oversized-page image, used for the size-limit path.
        huge = self.anomalies_dir / "oversized_page.jpg"
        image = Image.new("RGB", (5200, 7200), (250, 250, 250))
        draw = ImageDraw.Draw(image)
        draw.text((60, 60), "Oversized page for limit testing", fill=(0, 0, 0))
        image.save(huge, quality=55)
        self._entry(
            name="oversized_page",
            kind="anomaly",
            layout="n/a",
            path=huge,
            truth_path=None,
            page_count=1,
            notes="52x72 cm at 300 dpi equivalent; exercises the decoded-pixel guard.",
            expected_error="decompression_bomb",
        )

        # 8. DOCX that is a zip bomb: a tiny archive that expands enormously.
        bomb = self.anomalies_dir / "docx_zip_bomb.docx"
        _write_zip_bomb(bomb)
        self._entry(
            name="docx_zip_bomb",
            kind="anomaly",
            layout="n/a",
            path=bomb,
            truth_path=None,
            page_count=0,
            notes="A DOCX whose expansion ratio exceeds the guard; must be rejected before extraction.",
            expected_error="decompression_bomb",
        )

        # 9. Unsupported-but-plausible format.
        webp = self.anomalies_dir / "unsupported_format.webp"
        Image.new("RGB", (600, 800), (255, 255, 255)).save(webp, format="WEBP")
        self._entry(
            name="unsupported_format",
            kind="anomaly",
            layout="n/a",
            path=webp,
            truth_path=None,
            page_count=1,
            notes="WEBP is deliberately out of P0 scope; the error must name the supported set.",
            expected_error="unsupported_format",
        )

        # 10. Renamed DOCX pretending to be .doc (extension is not a conversion).
        renamed = self.anomalies_dir / "renamed_docx_as_doc.doc"
        shutil.copyfile(self.files_dir / "docx_paper_j.docx", renamed)
        self._entry(
            name="renamed_docx_as_doc",
            kind="anomaly",
            layout="n/a",
            path=renamed,
            truth_path=None,
            page_count=0,
            notes="A real DOCX renamed to .doc: content sniffing must win over the extension.",
            expected_error=None,
        )

        # 11. Encrypted, and 12. a directory-in-zip style nested archive.
        nested = self.anomalies_dir / "docx_nested_archive.docx"
        _write_nested_archive(nested, self.files_dir / "docx_paper_j.docx")
        self._entry(
            name="docx_nested_archive",
            kind="anomaly",
            layout="n/a",
            path=nested,
            truth_path=None,
            page_count=0,
            notes="DOCX containing an extra nested archive part; conversion must ignore or refuse safely.",
            expected_error=None,
        )

    # -- manifest ----------------------------------------------------------
    def write_manifest(self, extra: dict[str, Any] | None = None) -> Path:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        manifest = {
            "generator": "rr_m0.corpus_generator",
            "generator_version": "1.0.0",
            "corpus_kind": "synthetic-authored",
            "licence_note": (
                "All documents in this corpus were written and laid out by this "
                "repository's generator. They contain no third-party text and may be "
                "redistributed. They do not represent real research."
            ),
            "counts": counts,
            "total_files": len(self.entries),
            "entries": [entry.to_dict() for entry in self.entries],
        }
        if extra:
            manifest.update(extra)
        target = self.root / "manifest.json"
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        return target


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _encode(image: Image.Image, fmt: str, jpeg_quality: int) -> bytes:
    import io

    buffer = io.BytesIO()
    if fmt.lower() in ("jpg", "jpeg"):
        image.convert("RGB").save(buffer, format="JPEG", quality=jpeg_quality)
    else:
        image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _add_noise(image: Image.Image, sigma: float, speckles: int, rng: random.Random) -> Image.Image:
    """Add Gaussian sensor noise and dust speckles.

    The noise is generated with a vectorised NumPy generator.  The previous
    implementation built a nested Python list comprehension over every pixel,
    which for a 300 dpi A4 page means ~26 million interpreter steps per page and
    dominated corpus generation time; the two produce equivalent output for this
    purpose.
    """
    import numpy as np

    array = np.asarray(image).astype("float32")
    if sigma > 0:
        generator = np.random.default_rng(rng.randrange(1 << 32))
        array = array + generator.normal(0.0, sigma, array.shape)
    array = np.clip(array, 0, 255).astype("uint8")
    out = Image.fromarray(array, "RGB")
    if speckles:
        draw = ImageDraw.Draw(out)
        width, height = out.size
        for _ in range(speckles):
            x = rng.randrange(width)
            y = rng.randrange(height)
            value = rng.randrange(30, 120)
            draw.point((x, y), fill=(value, value, value))
            if rng.random() < 0.15:
                draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(value, value, value))
    return out


def _apply_vignette(image: Image.Image) -> Image.Image:
    import numpy as np

    array = np.asarray(image).astype("float32")
    height, width, _ = array.shape
    ys = np.linspace(-1.0, 1.0, height)[:, None]
    xs = np.linspace(-1.0, 1.0, width)[None, :]
    radius = np.sqrt(xs**2 + ys**2) / math_sqrt2()
    mask = np.clip(1.0 - 0.22 * radius**2, 0.0, 1.0)
    array = array * mask[:, :, None]
    return Image.fromarray(np.clip(array, 0, 255).astype("uint8"), "RGB")


def math_sqrt2() -> float:
    return 2.0**0.5


def _legacy_doc_bytes() -> bytes:
    """Bytes with the OLE2 compound-file magic but no usable content.

    That is exactly what a real Word 97 file looks like to a sniffer, which is
    what the format detector must recognise.
    """
    header = bytes.fromhex("D0CF11E0A1B11AE1")
    padding = bytes(512 - len(header) - 8)
    return header + b"\x00" * 24 + padding + b"WordDocument" + bytes(2048)


def _write_zip_bomb(path: Path) -> None:
    """A DOCX-shaped zip whose payload expands far beyond the guard."""
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"\x00" * (220 * 1024 * 1024))


def _write_nested_archive(path: Path, inner: Path) -> None:
    import zipfile

    payload = inner.read_bytes()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr("word/embeddings/nested.docx", payload)


# ---------------------------------------------------------------------------
# corpus definition
# ---------------------------------------------------------------------------


def build_corpus(root: Path) -> Path:
    gen = CorpusGenerator(root)

    # --- 8 born-digital text PDFs ----------------------------------------
    gen.build_text_pdf(
        "pdf_a_single_column", "Structured Extraction for Single-Column Articles",
        "A. Author, B. Author, and C. Author", 1, content.paper_a(),
        notes="Baseline single-column born-digital PDF with figure, table and references.",
    )
    gen.build_text_pdf(
        "pdf_b_two_column", "Reading Order Recovery in Two-Column Layouts",
        "D. Researcher and E. Scholar", 2, content.paper_b(),
        notes="Two-column PDF with display formula, table and figure.",
    )
    gen.build_text_pdf(
        "pdf_c_formula_footnote", "Long Paragraphs, Footnotes and Equations",
        "F. Analyst, G. Engineer, and H. Statistician", 1, content.paper_c(),
        notes="Single column; long paragraph crosses a page boundary; two footnotes.",
        extra_truth={"contains_cross_page_paragraph": True},
    )
    gen.build_text_pdf(
        "pdf_d_mixed_language", "中英文混排文档的阅读顺序恢复 Mixed-Script Reading Order",
        "I. Writer and J. Reader", 1, content.paper_d(),
        notes="Single column with a Chinese/English mixed abstract and body.",
        extra_truth={"contains_mixed_language": True},
    )
    gen.build_text_pdf(
        "pdf_e_hyphenation", "Hyphenation in Extracted Research Text",
        "K. Linguist and L. Engineer", 1, content.paper_e(),
        notes="Single column, hyphen-heavy: wrapped compounds must not be fused.",
        extra_truth={"contains_hyphenation": True},
    )
    gen.build_text_pdf(
        "pdf_f_two_column_wide_table", "Wide Objects in Two-Column Articles",
        "M. Editor and N. Reviewer", 2, content.paper_f(),
        notes="Two-column PDF with a full-width table and a rotated final page.",
        page_rotations={4: 90},
        extra_truth={"contains_rotated_page": True},
    )
    gen.build_text_pdf(
        "pdf_g_deep_sections", "A Short Note on Section Trees",
        "O. Author", 1, content.paper_g(),
        notes="Three-level heading nesting; short sections.",
        extra_truth={"contains_deep_sections": True},
    )
    gen.build_text_pdf(
        "pdf_h_no_references", "Technical Report Without a Reference List",
        "P. Author and Q. Author", 2, content.paper_h(),
        notes="Two column, no references section, no acknowledgements.",
    )

    # --- 6 scanned / mixed PDFs ------------------------------------------
    # Each reuses a text PDF as the source of truth so the OCR suite can be
    # scored against exactly the text that was rendered.
    source_a = gen.files_dir / "pdf_a_single_column.pdf"
    source_b = gen.files_dir / "pdf_b_two_column.pdf"
    source_e = gen.files_dir / "pdf_e_hyphenation.pdf"
    spec_a = _reload_spec(gen, "pdf_a_single_column")
    spec_b = _reload_spec(gen, "pdf_b_two_column")
    spec_e = _reload_spec(gen, "pdf_e_hyphenation")

    gen.build_scanned_pdf(
        "scan_a_clean_300dpi", source_a, spec_a, CLEAN_SCAN,
        notes="Clean 300 dpi scan of the single-column paper: the primary OCR baseline.",
    )
    gen.build_scanned_pdf(
        "scan_b_two_column_noisy", source_b, spec_b, NOISY_SCAN,
        notes="Two-column scan with heavy noise and speckle.",
    )
    gen.build_scanned_pdf(
        "scan_c_skewed", source_e, spec_e, SKEWED_SCAN,
        notes="Scanned with 1.8 degree skew: exercises rotation handling.",
    )
    gen.build_scanned_pdf(
        "scan_d_low_dpi", source_a, spec_a, CLEAN_SCAN_LOW,
        notes="200 dpi scan; reported separately, never merged into the clean figure.",
    )
    gen.build_scanned_pdf(
        "scan_e_grayscale", source_b, spec_b, GRAYSCALE_SCAN,
        notes="High-contrast grayscale scan.",
    )
    gen.build_scanned_pdf(
        "mixed_f_hybrid", source_b, spec_b, CLEAN_SCAN,
        notes="Hybrid PDF: pages 0 and 2 are rasterised, the rest keep their text layer.",
        mixed_scan_pages=[0, 2],
    )

    # --- 4 standalone images ---------------------------------------------
    gen.build_image(
        "img_a_clean_png", source_a, spec_a, CLEAN_SCAN, page_index=0,
        image_format="png", notes="Clean PNG of the single-column first page.",
    )
    gen.build_image(
        "img_b_photo_jpeg", source_a, spec_a, PHOTO_SCAN, page_index=0,
        image_format="jpeg", notes="Photograph-like JPEG with skew, vignette and noise.",
    )
    gen.build_image(
        "img_c_low_dpi_jpeg", source_b, spec_b, CLEAN_SCAN_LOW, page_index=0,
        image_format="jpeg", notes="200 dpi JPEG of the two-column first page.",
    )
    gen.build_image(
        "img_d_cropped_png", source_a, spec_a, CLEAN_SCAN, page_index=0,
        image_format="png", crop_fraction=0.55,
        notes="Top 55% of the page only: exercises partial-page anchoring.",
    )

    # --- 6 DOCX ----------------------------------------------------------
    gen.build_docx(
        "docx_paper_j", "Word Documents Without Fixed Pagination",
        "R. Author, S. Author", content.paper_j(),
        notes="Baseline DOCX with table, image, equation and footnote parts.",
    )
    gen.build_docx(
        "docx_paper_a", "Structured Extraction for Single-Column Articles",
        "A. Author, B. Author, and C. Author", content.paper_a(),
        notes="Same content as pdf_a, used to compare conversion against a known layout.",
    )
    gen.build_docx(
        "docx_paper_d_mixed", "中英文混排文档的阅读顺序恢复",
        "I. Writer and J. Reader", content.paper_d(),
        notes="Mixed-script DOCX.",
    )
    gen.build_docx(
        "docx_paper_c_math", "Long Paragraphs, Footnotes and Equations",
        "F. Analyst, G. Engineer", content.paper_c(),
        notes="DOCX with display equations and footnote parts.",
    )
    gen.build_docx(
        "docx_paper_g_sections", "A Short Note on Section Trees",
        "O. Author", content.paper_g(),
        notes="Deep heading nesting in DOCX form.",
    )
    gen.build_docx(
        "docx_paper_f_table", "Wide Objects in Two-Column Articles",
        "M. Editor and N. Reviewer", content.paper_f(),
        notes="DOCX with a wide table and an embedded image.",
    )

    # --- anomalies --------------------------------------------------------
    gen.build_anomalies(source_a)

    manifest = gen.write_manifest(
        {
            "source_documents": {
                "pdf_a_single_column": "pdf_a_single_column.pdf",
                "pdf_b_two_column": "pdf_b_two_column.pdf",
                "pdf_e_hyphenation": "pdf_e_hyphenation.pdf",
            },
            "holdout_note": (
                "Development and holdout split: files whose name ends in a letter "
                "from a to e form the development set used while tuning heuristics; "
                "f to j form the holdout set. benchmarks/holdout.json records the "
                "split explicitly so it can be checked mechanically."
            ),
        }
    )
    _write_holdout(gen)
    verify_truth_is_not_empty(gen.truth_dir)
    return manifest


def verify_truth_is_not_empty(truth_dir: Path) -> None:
    """Fail if any page's recorded visible text is empty.

    A silently empty ground truth reported a character error rate of ``None``
    rather than an error, which is the worst possible failure mode for an
    acceptance harness: the metric looked like it ran.  Checking it here means a
    regression is caught at generation time.
    """
    problems: list[str] = []
    for path in sorted(truth_dir.glob("*.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        pages = truth.get("pages")
        if pages is None:
            continue  # DOCX truth has no pagination
        for page in pages:
            visible = page.get("visible_text", "")
            if not visible.strip():
                problems.append(f"{path.name} page {page.get('index')}")
    if problems:
        raise AssertionError(
            "ground truth has pages with no visible text, which would make every "
            "per-page comparison vacuous:\n  " + "\n  ".join(problems)
        )
    print(f"truth check  : every page in {len(list(truth_dir.glob('*.json')))} file(s) has visible text")


def _reload_spec(gen: CorpusGenerator, name: str) -> DocumentSpec:
    """Rebuild the in-memory spec by regenerating it (cheap and exact)."""
    truth = json.loads((gen.truth_dir / f"{name}.json").read_text(encoding="utf-8"))
    spec = DocumentSpec(
        name=truth["name"],
        title=truth["title"],
        authors=truth["authors"],
        layout=LayoutKind(truth["layout"]),
        columns=truth["columns"],
    )
    for page in truth["pages"]:
        page_spec = PageSpec(index=page["index"], rotation=page["rotation"])
        page_spec.header_text = page.get("header_text")
        page_spec.footer_text = page.get("footer_text")
        page_spec.printed_label = page.get("printed_label")
        page_spec.figure_boxes = page.get("figure_boxes", [])
        page_spec.table_boxes = page.get("table_boxes", [])
        page_spec.has_formula = page.get("has_formula", False)
        for block in page["blocks"]:
            block_spec = BlockSpec(
                index=block["index"],
                type=block["type"],
                text=block["text"],
                page_index=block["page_index"],
            )
            block_spec.reading_order = block.get("reading_order", 0)
            block_spec.section_path = block.get("section_path", [])
            block_spec.level = block.get("level")
            # The drawn lines must be restored too: `visible_text` and
            # `text_on_page` are derived from them, and a spec rebuilt without
            # lines produces empty per-page text.  That silently made every
            # scanned document's ground truth empty, which in turn reported a
            # character error rate of `None` instead of failing loudly.
            line_boxes = block.get("line_bboxes", [])
            line_texts = block.get("line_texts", [])
            line_pages = block.get("line_page_indices", [])
            for line_index, line_text in enumerate(line_texts):
                box = line_boxes[line_index] if line_index < len(line_boxes) else [0, 0, 0, 0]
                spec_line = LineSpec(
                    text=line_text,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                    font="restored",
                    size=0.0,
                    block_index=block_spec.index,
                )
                spec_line.page_index = (  # type: ignore[attr-defined]
                    line_pages[line_index] if line_index < len(line_pages) else page["index"]
                )
                block_spec.lines.append(spec_line)
            page_spec.blocks.append(block_spec)
        page_spec.decorations.extend(page.get("decorations", []))
        spec.pages.append(page_spec)
    return spec


def _write_holdout(gen: CorpusGenerator) -> None:
    """Record the development/holdout split explicitly (spec 12.1)."""
    develop: list[str] = []
    holdout: list[str] = []
    for entry in gen.entries:
        if entry.kind in ("anomaly",):
            continue
        suffix = entry.name.rsplit("_", 1)[-1]
        letter = suffix[:1].lower()
        if letter and letter in "abcde" and entry.name.split("_")[0] in ("pdf", "scan", "img", "docx"):
            develop.append(entry.name)
        else:
            holdout.append(entry.name)
    payload = {
        "note": (
            "Files used while tuning heuristics (development) versus files reserved "
            "for reporting (holdout). The split is by the trailing letter of the "
            "corpus name and is written out so a checker can verify that no holdout "
            "file was used for tuning."
        ),
        "development": sorted(develop),
        "holdout": sorted(holdout),
    }
    (gen.root / "holdout.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the labelled acceptance corpus")
    parser.add_argument(
        "--root",
        default="benchmarks/corpus",
        help="output directory (default: benchmarks/corpus)",
    )
    parser.add_argument("--clean", action="store_true", help="delete the root first")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if args.clean and root.exists():
        shutil.rmtree(root)
    manifest = build_corpus(root)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    print(f"corpus root : {root}")
    print(f"files       : {data['total_files']}")
    for kind, count in sorted(data["counts"].items()):
        print(f"  {kind:14} {count}")
    total_bytes = sum(entry["size_bytes"] for entry in data["entries"])
    print(f"total size  : {total_bytes / 1024 / 1024:.2f} MiB")
    print(f"manifest    : {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
