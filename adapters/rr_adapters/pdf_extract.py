"""PDF text extraction with exact coordinate transforms.

Responsibilities (and the boundary against :mod:`document_core.layout`):

* extract text lines, their boxes and font metadata from a PDF page,
* convert every box out of PDF user space into the frozen display-normalised
  contract, recording the transform that was applied,
* report per-page evidence about whether the text layer is usable,
* render page previews and thumbnails as derived resources.

Reading order, paragraph assembly and section trees are **not** decided here;
they are the job of ``document_core.layout``.  This module only produces honest
:class:`~document_core.layout.TextLine` objects and page metadata.

Two details matter for correctness:

* PDF user space has its origin at the bottom-left with y growing upwards, while
  the contract is top-left with y growing downwards.  The conversion is not a
  simple flip once ``/Rotate`` is involved, so it goes through
  :class:`~document_core.coords.PageGeometry` rather than being done ad hoc.
* A PDF page may carry CropBox offsets.  Boxes are expressed relative to the
  CropBox in user space; using the MediaBox origin silently shifts every anchor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf

from document_core.cleaning import garbage_ratio, looks_like_garbage
from document_core.coords import AffineTransform, BBox, PageGeometry
from document_core.layout import TextLine

__all__ = [
    "PdfPageInfo",
    "PdfExtraction",
    "PdfInspection",
    "PdfTextExtractor",
    "PdfCorruptError",
    "page_geometry",
    "is_encrypted",
    "inspect_pdf",
    "page_is_probably_scanned",
    "render_page",
    "render_thumbnail",
    "render_page_images",
]

#: A page is treated as scanned when its text layer carries fewer printable
#: characters than this, or when the extracted text is mostly garbage.
MIN_USABLE_TEXT_CHARS = 40
#: Share of the page area that must be covered by images for the page to be a
#: scan candidate even when it has some text.
IMAGE_COVER_THRESHOLD = 0.45


@dataclass
class PdfPageInfo:
    index: int
    width_pt: float
    height_pt: float
    rotation: int
    crop: tuple[float, float, float, float] | None
    text_chars: int
    garbage_ratio: float
    image_count: int
    image_coverage: float
    has_drawings: bool
    text_usable: bool
    likely_scanned: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "rotation": self.rotation,
            "crop": self.crop,
            "text_chars": self.text_chars,
            "garbage_ratio": round(self.garbage_ratio, 4),
            "image_count": self.image_count,
            "image_coverage": round(self.image_coverage, 4),
            "has_drawings": self.has_drawings,
            "text_usable": self.text_usable,
            "likely_scanned": self.likely_scanned,
            "reasons": self.reasons,
        }


@dataclass
class PdfExtraction:
    page_count: int
    page_infos: list[PdfPageInfo]
    lines_by_page: dict[int, list[TextLine]]
    geometry_by_page: dict[int, PageGeometry]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_lines(self) -> list[TextLine]:
        return [line for page in sorted(self.lines_by_page) for line in self.lines_by_page[page]]


def is_encrypted(path: str | Path) -> bool:
    """Whether a PDF needs a password to read.

    ``pymupdf`` will happily open a document with an empty user password, so the
    check is whether it still requires authentication.
    """
    doc = pymupdf.open(str(path))
    try:
        return bool(doc.needs_pass)
    finally:
        doc.close()


class PdfCorruptError(ValueError):
    """The file cannot be opened as a PDF at all."""


@dataclass(frozen=True)
class PdfInspection:
    """Cheap facts about a PDF, known before committing to a full parse."""

    page_count: int
    encrypted: bool
    needs_password: bool


def inspect_pdf(path: str | Path) -> PdfInspection:
    """Read a PDF's page count and encryption state without extracting anything.

    This runs at import so an encrypted or absurdly long document is refused while the
    user is still watching, rather than after a parse job has been queued and a worker
    has spent time on it.  Reading the page tree is cheap; extracting text is not.

    The bytes are read into memory and handed to PyMuPDF as a *stream* rather than a path.
    That is not an optimisation; it is a fix for a Windows defect measured here:

    PyMuPDF keeps the file open for as long as the exception that references its internals
    is alive.  Deleting the just-written file from inside the ``except`` block that is
    handling such an exception therefore fails with ``WinError 32``, and — measured, not
    assumed — so does deleting it anywhere while the exception object is still held.  A
    refused import would then leave an undeletable file behind, and the cleanup failure
    would replace the real error with a sharing violation.  Opening from a buffer means no
    file handle exists to leak, whatever happens to the exception afterwards.

    The read is bounded by the upload limit the caller already enforced, so the cost is
    transient and capped.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PdfCorruptError(f"cannot read the file: {exc}") from None
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises several unrelated exception types here
        # `from None`: the cause would otherwise keep a reference to PyMuPDF's internals
        # (and the buffer) alive, and would only add noise to the message.
        raise PdfCorruptError(f"cannot open as a PDF: {exc}") from None
    try:
        needs_password = bool(doc.needs_pass)
        try:
            page_count = int(doc.page_count)
        except Exception:
            # An encrypted document may not expose a page tree until it is unlocked.
            page_count = 0
        return PdfInspection(
            page_count=page_count,
            encrypted=bool(doc.is_encrypted),
            needs_password=needs_password,
        )
    finally:
        doc.close()


def page_geometry(page: pymupdf.Page) -> PageGeometry:
    """Build a :class:`PageGeometry` from a PyMuPDF page.

    ``rotation`` is the *effective* rotation (the page's own ``/Rotate`` plus any
    rotation the document applies), because that is what determines the
    displayed orientation.
    """
    rect = page.rect
    crop = page.cropbox
    media = page.mediabox
    # page.rect already accounts for rotation; recover the unrotated crop size
    # so the geometry class can apply the rotation itself.
    rotation = int(page.rotation or 0) % 360
    if rotation in (90, 270):
        unrotated_width, unrotated_height = crop.height, crop.width
    else:
        unrotated_width, unrotated_height = crop.width, crop.height
    crop_box = None
    if (crop.x0, crop.y0, crop.x1, crop.y1) != (media.x0, media.y0, media.x1, media.y1):
        crop_box = BBox(crop.x0, crop.y0, crop.x1, crop.y1)
    return PageGeometry(
        media_width=unrotated_width,
        media_height=unrotated_height,
        rotation=rotation,
        crop=crop_box,
    )


def page_is_probably_scanned(page: pymupdf.Page, text: str) -> tuple[bool, list[str]]:
    """Decide whether a page's text layer should be distrusted (spec 5.2).

    Returns ``(likely_scanned, reasons)``.  A page with no images and plenty of
    clean text is born-digital; a page with a large image and no text is scanned;
    a page with a text layer that is mostly replacement characters is the
    dangerous middle case and is treated as scanned.
    """
    reasons: list[str] = []
    stripped = text.strip()
    ratio = garbage_ratio(stripped)
    image_rects = _image_coverage(page)
    coverage = sum(_rect_area(r) for r in image_rects) / max(1.0, page.rect.width * page.rect.height)

    if len(stripped) < MIN_USABLE_TEXT_CHARS:
        reasons.append(f"text layer has only {len(stripped)} characters")
        if coverage > IMAGE_COVER_THRESHOLD:
            reasons.append(f"images cover {coverage:.0%} of the page")
        return True, reasons
    if ratio > 0.05:
        reasons.append(f"{ratio:.1%} of characters are replacement or control characters")
        return True, reasons
    if looks_like_garbage(stripped):
        reasons.append("text layer looks like symbol soup despite having length")
        return True, reasons
    if coverage > IMAGE_COVER_THRESHOLD and len(stripped) < 200:
        reasons.append(
            f"images cover {coverage:.0%} of the page but only {len(stripped)} characters are extractable"
        )
        return True, reasons
    return False, reasons


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _image_coverage(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    rects: list[tuple[float, float, float, float]] = []
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if bbox:
            rects.append((bbox[0], bbox[1], bbox[2], bbox[3]))
    return rects


class PdfTextExtractor:
    """Extract text lines and page evidence from a PDF."""

    def __init__(
        self,
        *,
        min_usable_chars: int = MIN_USABLE_TEXT_CHARS,
        strip_repeated_marginals: bool = False,
    ) -> None:
        self.min_usable_chars = min_usable_chars
        # Repeated-margin detection is deliberately *not* done here: the layout
        # engine labels furniture after it can see every page, and doing it twice
        # would drop evidence the layout stage needs.
        self.strip_repeated_marginals = strip_repeated_marginals

    def extract(
        self,
        path: str | Path,
        *,
        page_indices: Sequence[int] | None = None,
    ) -> PdfExtraction:
        doc = pymupdf.open(str(path))
        try:
            if doc.needs_pass:
                raise ValueError("document is encrypted")
            selected = list(range(doc.page_count)) if page_indices is None else list(page_indices)
            infos: list[PdfPageInfo] = []
            lines_by_page: dict[int, list[TextLine]] = {}
            geometry: dict[int, PageGeometry] = {}
            warnings: list[str] = []
            for index in selected:
                page = doc.load_page(index)
                raw_text = page.get_text("text") or ""
                scanned, reasons = page_is_probably_scanned(page, raw_text)
                geo = page_geometry(page)
                geometry[index] = geo
                lines = self._lines_from_page(page, index, geo)
                lines_by_page[index] = lines
                image_rects = _image_coverage(page)
                coverage = sum(_rect_area(r) for r in image_rects) / max(
                    1.0, page.rect.width * page.rect.height
                )
                infos.append(
                    PdfPageInfo(
                        index=index,
                        width_pt=geo.display_width,
                        height_pt=geo.display_height,
                        rotation=geo.rotation,
                        crop=geo.crop.as_tuple() if geo.crop else None,
                        text_chars=len(raw_text.strip()),
                        garbage_ratio=garbage_ratio(raw_text),
                        image_count=len(image_rects),
                        image_coverage=coverage,
                        has_drawings=bool(page.get_drawings()),
                        text_usable=not scanned,
                        likely_scanned=scanned,
                        reasons=reasons,
                    )
                )
                if scanned and lines:
                    warnings.append(
                        f"page {index}: text layer distrusted ({'; '.join(reasons)}); "
                        "OCR is required for reliable text"
                    )
            metadata = dict(doc.metadata or {})
            return PdfExtraction(
                page_count=doc.page_count,
                page_infos=infos,
                lines_by_page=lines_by_page,
                geometry_by_page=geometry,
                metadata=metadata,
                warnings=warnings,
            )
        finally:
            doc.close()

    # -- line extraction ---------------------------------------------------
    def _lines_from_page(
        self,
        page: pymupdf.Page,
        page_index: int,
        geo: PageGeometry,
    ) -> list[TextLine]:
        """Turn PyMuPDF's span output into contract-space text lines."""
        try:
            data = page.get_text("dict", sort=False)
        except Exception:
            return []
        to_norm = geo.raw_to_norm()
        lines: list[TextLine] = []
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(span.get("text", "") for span in spans)
                if not text.strip():
                    continue
                # Merge span boxes: a line may mix roman, italic and bold runs.
                x0 = min(s["bbox"][0] for s in spans)
                y0 = min(s["bbox"][1] for s in spans)
                x1 = max(s["bbox"][2] for s in spans)
                y1 = max(s["bbox"][3] for s in spans)
                raw_box = _text_frame_to_raw(geo, BBox(x0, y0, x1, y1))
                norm_box = _transform_box(to_norm, raw_box)
                sizes = [s.get("size", 0.0) for s in spans if s.get("size")]
                dominant = max(spans, key=lambda s: len(s.get("text", "")))
                lines.append(
                    TextLine(
                        text=text,
                        bbox=norm_box,
                        page_index=page_index,
                        # Inline superscripts/subscripts must not turn a body
                        # line into a different paragraph type size.
                        font_size=round(dominant.get("size", 0), 3) if sizes else None,
                        font_name=dominant.get("font"),
                        bold=_looks_bold(dominant),
                        italic=_looks_italic(dominant),
                        colour=dominant.get("color"),
                        block_hint=block.get("number"),
                        source="text",
                        non_horizontal=abs(line.get("dir", (1, 0))[1]) > .5,
                        spans=[
                            (
                                _transform_box(to_norm, BBox(*s["bbox"])).x0,
                                _transform_box(to_norm, BBox(*s["bbox"])).x1,
                                s.get("text", ""),
                            )
                            for s in spans
                        ],
                    )
                )
        return lines


def _text_frame_to_raw(geo: PageGeometry, box: BBox) -> BBox:
    """Convert a PyMuPDF text box into raw PDF space.

    ``page.get_text`` reports boxes in the **unrotated media frame with a top-left
    origin and y increasing downwards**, which is not the frame
    :meth:`PageGeometry.raw_to_norm` expects.  Raw PDF space has its origin at the
    bottom-left with y increasing upwards (the coordinate contract, ADR 0008), so
    the y axis has to be flipped before the transform is applied.  The x axis needs
    no change because both frames start at the left edge.

    **This was a real bug, and it mirrored every line vertically.**  The adapter used
    to pass PyMuPDF's boxes straight into ``raw_to_norm``, so on an unrotated page a
    line drawn at the top was reported at the bottom and vice versa.  It was found by
    measuring the adapter's box for a page's title against the pixels the renderer
    produced for that title: the adapter said rows 1054-1086 of 1170 while the title
    was rendered at rows 92-110.  The rotation-aware version of this check
    (``scripts/verify_coordinate_fix.py``) confirms this conversion lands within
    0.0013 of the rendered marker for all four rotations, whereas the previous
    handling was wrong on every page including unrotated ones.

    Two properties of ``get_text`` were measured rather than assumed, because the
    documentation was not available:

    * the frame is the **unrotated** media frame - rotating a page by 90, 180 or 270
      degrees does not change the box ``get_text`` returns for the same text, while
      the renderer does rotate it.  Rotation is therefore applied by
      ``raw_to_norm`` and must not be applied here as well;
    * the frame is relative to the **media** box, so the flip uses the media height
      rather than the crop height.
    """
    return BBox(box.x0, geo.media_height - box.y1, box.x1, geo.media_height - box.y0)


def _transform_box(transform: AffineTransform, box: BBox) -> BBox:
    corners = transform.apply_polygon(
        ((box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1))
    )
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    from document_core.coords import clamp

    return BBox(
        clamp(min(xs)), clamp(min(ys)), clamp(max(xs)), clamp(max(ys))
    )


def _looks_bold(span: dict[str, Any]) -> bool:
    name = str(span.get("font", ""))
    flags = int(span.get("flags", 0) or 0)
    return bool(flags & 2**4) or bool(re.search(r"bold|black|heavy|semibold", name, re.I))


def _looks_italic(span: dict[str, Any]) -> bool:
    name = str(span.get("font", ""))
    flags = int(span.get("flags", 0) or 0)
    return bool(flags & 2**1) or bool(re.search(r"italic|oblique", name, re.I))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_page(
    doc: pymupdf.Document,
    page_index: int,
    *,
    dpi: int = 120,
    fmt: str = "png",
) -> bytes:
    """Render one page for preview, honouring the page's rotation."""
    page = doc.load_page(page_index)
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    if fmt == "jpg":
        return pixmap.tobytes("jpg", jpg_quality=80)
    return pixmap.tobytes("png")


def render_thumbnail(
    doc: pymupdf.Document,
    page_index: int,
    *,
    max_width: int = 180,
) -> bytes:
    page = doc.load_page(page_index)
    zoom = max_width / max(1.0, page.rect.width)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")


def render_page_images(
    path: Path,
    *,
    dpi: int = 120,
    thumbnail_max_width: int = 180,
    page_indices: Sequence[int] | None = None,
) -> Iterable[tuple[int, bytes, bytes]]:
    """Yield ``(page_index, preview_png, thumbnail_png)`` one page at a time.

    A generator rather than a dict on purpose: a 2000-page paper would otherwise hold every
    rendered page in memory at once before any of them was written.  Here the caller writes each
    page and drops it.

    The pixmaps come from :func:`render_page` and :func:`render_thumbnail`, which use PyMuPDF's
    own rendering.  That applies the page's ``/Rotate`` and renders the page's visible box, so
    the image is the page as a reader sees it - which is the frame the normalised span
    coordinates are expressed in, and therefore the frame the overlay is positioned against.
    """
    doc = pymupdf.open(path)
    try:
        indices = range(doc.page_count) if page_indices is None else page_indices
        for index in indices:
            if index < 0 or index >= doc.page_count:
                raise IndexError(f"page {index} is outside 0..{doc.page_count - 1}")
            yield (
                index,
                render_page(doc, index, dpi=dpi),
                render_thumbnail(doc, index, max_width=thumbnail_max_width),
            )
    finally:
        doc.close()
