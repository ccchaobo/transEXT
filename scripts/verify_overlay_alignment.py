"""Do the block overlays actually sit on the text?

ADR 0016 puts the source pane's boxes on top of a server-rendered page image, positioned as
percentages of the span's normalised bbox.  There is no browser automation here, so "the overlay
lines up" cannot be checked by looking at it.  This measures it.

WHY NOT SIMPLY "IS THERE INK IN THE BOX"

The first version of this script scored each box as ``ink_inside / ink_on_the_whole_page`` and
required 3x.  It reported 37% of spans passing, which was a statement about the metric, not about
the overlay: these corpus pages are densely set two-column text where 5-7% of *every* pixel is
ink, so a line's box holds only about 1.5-2x the page average even when it is exactly on the
line.  The page average is not blank paper, and treating it as one made a correct overlay look
broken.

The second version compared each box against its own vertical mirror, per span.  That is a real
test - a mirrored overlay is precisely what it looks for - but at the span level it is noisy:
on a dense page a single body line near the bottom can legitimately hold less ink than whatever
happens to sit at the top, so ~3.5% of spans "fail" while the overlay is correct.

WHAT IS MEASURED INSTEAD

The decisive measurement is per PAGE, not per span, and it uses the whole page's shape:

* ``ink[y]``  the page's ink density in each row (and each column);
* ``cover[y]`` the fraction of that row which the span boxes cover.

If the boxes are where the text is, the two profiles rise and fall together.  If the coordinates
are mirrored about the horizontal axis, the coverage profile is reversed against the ink profile
and the correlation goes **negative** - the sign flips, which is a much larger and less ambiguous
effect than any density comparison.

The profiles are compared in BANDS (24 horizontal and 24 vertical slices), not row by row.  Row
by row does not work, and the reason is worth recording because it looked like a failure of the
overlay: ``ink[y]`` oscillates with the line pitch - a text row is inked, the leading beneath it
is blank - while ``cover[y]`` is a constant step across a whole block, leading included.  That
high-frequency disagreement is most of the variance, and it drove the row correlation to +0.44 on
pages whose columns correlated at +0.93.  It was measuring the rasterisation of leading, not the
placement of boxes.  Averaging within a band removes the line pitch and leaves the page's real
structure - margins against text - while keeping the sign flip a mirror produces.

Each axis is reported twice: against the coverage profile as given (``self``) and against that
profile reversed (``mirrored``).  A correct overlay has ``self`` clearly above ``mirrored``, and a
mirrored overlay swaps them, so the pair is discriminating on its face rather than resting on a
threshold nobody can check.  One caveat is stated in the output: on a two-column page the
horizontal profile is nearly symmetric about the page centre, so the column test has little power
against a horizontal mirror even though it is a good check that boxes are on text.
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from rr_api import create_app
from rr_services import Settings
from rr_worker import Worker

CORPUS = Path("benchmarks/corpus")

#: Luminance below this counts as ink.  The images are anti-aliased greyscale on white, so this
#: sits well below the paper and well above the darkest antialiased fringe.
INK_THRESHOLD = 140

#: How far the page centre a box must be displaced before its reflection is a meaningful control.
MIN_DISPLACEMENT = 0.15

#: ``self`` may be this fraction of the reflection's density and still count as "not worse".
REFLECTION_TOLERANCE = 0.8

#: A correct overlay puts the ink and coverage profiles in step.  A mirror reverses one of them.
REQUIRED_CORRELATION = 0.6

#: The share of pages that must show the positive correlation.
REQUIRED_PAGE_SHARE = 0.9

#: Slices per axis.  24 bands over a ~1300px page is ~55px, roughly three lines of body text at
#: this corpus's 12.4pt leading, which averages the line pitch away.
BANDS = 24


def banded(profile: np.ndarray, bands: int = BANDS) -> np.ndarray:
    """Mean of ``profile`` within each of ``bands`` equal slices."""
    edges = np.linspace(0, len(profile), bands + 1).astype(int)
    values = [profile[edges[i]:max(edges[i] + 1, edges[i + 1])].mean() for i in range(bands)]
    return np.asarray(values, dtype=np.float64)


def ink_rows(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Per-row and per-column ink density of the page."""
    grey = np.asarray(image.convert("L"), dtype=np.int16)
    ink = (grey < INK_THRESHOLD).astype(np.float64)
    return ink.mean(axis=1), ink.mean(axis=0)


def density(image: Image.Image, box: tuple[int, int, int, int] | None = None) -> float:
    grey = image.convert("L")
    if box is not None:
        grey = grey.crop(box)
    histogram = grey.histogram()
    total = sum(histogram)
    if not total:
        return 0.0
    return sum(histogram[:INK_THRESHOLD]) / total


def pixel_box(
    bbox: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    px0 = max(0, min(width - 1, int(x0 * width)))
    py0 = max(0, min(height - 1, int(y0 * height)))
    px1 = max(px0 + 1, min(width, int(x1 * width)))
    py1 = max(py0 + 1, min(height, int(y1 * height)))
    return px0, py0, px1, py1


def reflect_y(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    return (x0, 1.0 - y1, x1, 1.0 - y0)


def reflect_x(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    return (1.0 - x1, y0, 1.0 - x0, y1)


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() < 1e-9 or b.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def main() -> int:
    # A fresh data directory per run, for the same reason as in verify_frontend_contract.py: a
    # fixed path under the repository makes the result depend on what the previous run left
    # behind, and writes into the ``var/`` a running server may be using.  ``ignore_cleanup_errors``
    # because the SQLite engine keeps the file open on Windows and the unlink would fail after the
    # checks had already passed.
    with tempfile.TemporaryDirectory(
        prefix="rr-overlay-check-", ignore_cleanup_errors=True
    ) as tmp:
        return _check(Path(tmp) / "data")


def _check(data_dir: Path) -> int:
    settings = Settings(data_dir=data_dir)
    app = create_app(settings, serve_frontend=False)
    client = TestClient(app)

    pdfs = sorted((CORPUS / "files").glob("*.pdf"))
    pdfs = [p for p in pdfs if not p.name.startswith("anomaly_") and "scanned" not in p.name]
    if not pdfs:
        print("no corpus PDFs found")
        return 1

    spans: list[dict] = []
    pages: list[dict] = []
    aspect_failures: list[str] = []

    for pdf in pdfs:
        with pdf.open("rb") as stream:
            created = client.post(
                "/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")}
            )
        if created.status_code != 201:
            print(f"  skip {pdf.name}: HTTP {created.status_code}")
            continue
        document_id = created.json()["document"]["id"]
        worker = Worker(settings, app.state.workspace, app.state.queue, database=app.state.database)
        worker.serve(poll_seconds=0, max_jobs=1)

        detail = client.get(f"/api/v1/documents/{document_id}").json()
        for page_index in range(detail["page_count"] or 0):
            payload = client.get(f"/api/v1/documents/{document_id}/pages/{page_index}").json()
            page = payload["page"]
            response = client.get(
                f"/api/v1/documents/{document_id}/pages/{page_index}/preview.png"
            )
            if response.status_code != 200:
                print(f"  FAIL {pdf.name} p{page_index}: no preview")
                continue
            image = Image.open(io.BytesIO(response.content))
            width, height = image.size

            declared = page["display_width"] / page["display_height"] if page["display_height"] else 0
            rendered = width / height if height else 0
            if not declared or abs(declared - rendered) / declared >= 0.02:
                aspect_failures.append(
                    f"{pdf.name} p{page_index}: declared {declared:.4f} vs rendered {rendered:.4f}"
                )

            row_ink, column_ink = ink_rows(image)

            # Coverage profiles: for each row, the fraction of the page width covered by boxes;
            # for each column, the fraction of the height.  A row that a box crosses counts its
            # covered pixels, so a row with no boxes has coverage 0.
            row_cover = np.zeros(height)
            column_cover = np.zeros(width)
            for block in payload["blocks"]:
                if not (block["text"] or "").strip():
                    continue
                for span in block["spans"]:
                    if span["page_index"] != page_index:
                        continue
                    px0, py0, px1, py1 = pixel_box(tuple(span["bbox"]), width, height)
                    row_cover[py0:py1] += px1 - px0
                    column_cover[px0:px1] += py1 - py0
                    spans.append({"pdf": pdf.name, "page": page_index, "type": block["type"],
                                  "document_id": document_id,
                                  "bbox": tuple(span["bbox"]), "pixels": (px0, py0, px1, py1)})
            row_cover /= width
            column_cover /= height

            pages.append({
                "pdf": pdf.name,
                "page": page_index,
                "rows": correlation(banded(row_ink), banded(row_cover)),
                "rows_mirrored": correlation(banded(row_ink), banded(row_cover)[::-1]),
                "columns": correlation(banded(column_ink), banded(column_cover)),
                "columns_mirrored": correlation(banded(column_ink), banded(column_cover)[::-1]),
                "boxed_fraction": float(row_cover.mean()),
            })

    if not spans or not pages:
        print("nothing was measured")
        return 1

    # Per-span supporting evidence.  The margin reference and the reflections need the image
    # again, so they are computed in a second pass over the pages that were measured.
    own_vs_blank: list[float] = []
    y_tested = y_ok = x_tested = x_ok = 0

    for pdf_name, page_index, document_id in sorted(
        {(s["pdf"], s["page"], s["document_id"]) for s in spans}
    ):
        response = client.get(
            f"/api/v1/documents/{document_id}/pages/{page_index}/preview.png"
        )
        image = Image.open(io.BytesIO(response.content))
        width, height = image.size
        strips = [
            (0, 0, max(1, int(0.02 * width)), height),
            (width - max(1, int(0.02 * width)), 0, width, height),
            (0, 0, width, max(1, int(0.02 * height))),
            (0, height - max(1, int(0.02 * height)), width, height),
        ]
        strip_values = sorted(density(image, strip) for strip in strips)
        blank = strip_values[len(strip_values) // 2]

        for entry in [s for s in spans if s["pdf"] == pdf_name and s["page"] == page_index]:
            bbox = entry["bbox"]
            own = density(image, pixel_box(bbox, width, height))
            own_vs_blank.append(own)
            entry["own"] = own
            entry["blank"] = blank

            flip_y = tuple(reflect_y(bbox))
            y_moved = min(abs(bbox[1] - flip_y[1]), abs(bbox[3] - flip_y[3]))
            if y_moved >= MIN_DISPLACEMENT:
                y_tested += 1
                mirrored = density(image, pixel_box(flip_y, width, height))
                if own >= REFLECTION_TOLERANCE * mirrored:
                    y_ok += 1

            flip_x = tuple(reflect_x(bbox))
            x_moved = min(abs(bbox[0] - flip_x[0]), abs(bbox[2] - flip_x[2]))
            if x_moved >= MIN_DISPLACEMENT:
                x_tested += 1
                mirrored = density(image, pixel_box(flip_x, width, height))
                if own >= REFLECTION_TOLERANCE * mirrored:
                    x_ok += 1

    row_correlations = [p["rows"] for p in pages if not np.isnan(p["rows"])]
    column_correlations = [p["columns"] for p in pages if not np.isnan(p["columns"])]
    row_mirrored = [p["rows_mirrored"] for p in pages if not np.isnan(p["rows_mirrored"])]
    column_mirrored = [p["columns_mirrored"] for p in pages if not np.isnan(p["columns_mirrored"])]
    median_rows = float(np.median(row_correlations)) if row_correlations else float("nan")
    median_columns = float(np.median(column_correlations)) if column_correlations else float("nan")
    median_rows_mirrored = float(np.median(row_mirrored)) if row_mirrored else float("nan")
    median_columns_mirrored = (
        float(np.median(column_mirrored)) if column_mirrored else float("nan")
    )
    positive_rows = sum(1 for value in row_correlations if value >= REQUIRED_CORRELATION)
    positive_columns = sum(1 for value in column_correlations if value >= REQUIRED_CORRELATION)
    row_share = positive_rows / len(row_correlations) if row_correlations else 0.0
    column_share = positive_columns / len(column_correlations) if column_correlations else 0.0

    y_share = y_ok / y_tested if y_tested else 0.0
    x_share = x_ok / x_tested if x_tested else 0.0
    median_own = float(np.median(own_vs_blank)) if own_vs_blank else 0.0

    print(f"measured {len(spans)} spans over {len(pages)} rendered pages, {len(pdfs)} PDFs")
    print()
    print("PRIMARY: does the box coverage profile track the page's ink profile?")
    print("  A correct overlay makes these rise and fall together; a vertical mirror reverses one")
    print("  of them and drives the correlation negative.  Compared in 24 bands per axis, which")
    print("  averages away the line pitch that would otherwise dominate the row profile.")
    print(f"  rows    self r={median_rows:+.3f}   mirrored r={median_rows_mirrored:+.3f}")
    print(f"  columns self r={median_columns:+.3f}   mirrored r={median_columns_mirrored:+.3f}")
    print(f"  pages with row correlation    >= {REQUIRED_CORRELATION}: "
          f"{positive_rows}/{len(row_correlations)} = {row_share:.4f}")
    print(f"  pages with column correlation >= {REQUIRED_CORRELATION}: "
          f"{positive_columns}/{len(column_correlations)} = {column_share:.4f}")
    print("  note: on a two-column page the horizontal profile is nearly symmetric, so the")
    print("  column test shows boxes are on text but has little power against a horizontal mirror.")

    worst_pages = sorted(pages, key=lambda p: p["rows"])[:5]
    print("\n  worst 5 pages by row correlation:")
    for p in worst_pages:
        print(f"    rows={p['rows']:+.3f} (mirrored {p['rows_mirrored']:+.3f}) "
              f"columns={p['columns']:+.3f} (mirrored {p['columns_mirrored']:+.3f}) "
              f"boxed={p['boxed_fraction']:.3f} {p['pdf']} p{p['page']}")

    print()
    print("SUPPORTING: every span box against the page's blank margin strips")
    print(f"  margin strips are pure white on every page measured: "
          f"{sum(1 for s in spans if s['blank'] == 0)}/{len(spans)}")
    print(f"  median ink density inside span boxes: {median_own:.4f} "
          f"(> 0 means the box is not on blank paper)")
    print(f"  spans holding ink: {sum(1 for s in spans if s['own'] > 0)}/{len(spans)}")

    print()
    print("SUPPORTING: per-span reflection test (noisy at this granularity; see the module doc)")
    print(f"  not worse than vertical mirror:   {y_ok}/{y_tested} = {y_share:.4f} "
          f"(a true vertical mirror would score near 0.05)")
    print(f"  not worse than horizontal mirror: {x_ok}/{x_tested} = {x_share:.4f} "
          f"(a true horizontal mirror would score near 0.05)")

    print(f"\npage-image aspect ratio agreements: {len(pages)} pages, "
          f"{len(aspect_failures)} disagreed")
    for failure in aspect_failures[:6]:
        print(f"  FAIL {failure}")

    print()
    failures = []
    if aspect_failures:
        failures.append(f"{len(aspect_failures)} page aspect mismatch(es)")
    if median_rows < REQUIRED_CORRELATION:
        failures.append(f"median row correlation {median_rows:+.3f} < {REQUIRED_CORRELATION}")
    if row_share < REQUIRED_PAGE_SHARE:
        failures.append(f"only {row_share:.4f} of pages correlate on rows")
    if median_columns < REQUIRED_CORRELATION:
        failures.append(f"median column correlation {median_columns:+.3f} < {REQUIRED_CORRELATION}")
    if len(aspect_failures) == 0 and sum(1 for s in spans if s["own"] > 0) < len(spans):
        failures.append("some span boxes hold no ink at all")

    if failures:
        print("OVERLAY ALIGNMENT: FAIL - " + "; ".join(failures))
        return 1
    scored = len(row_correlations)
    print(f"OVERLAY ALIGNMENT: PASS - box coverage tracks page ink "
          f"(median row r={median_rows:+.3f} against its mirror's {median_rows_mirrored:+.3f}, "
          f"median column r={median_columns:+.3f} against {median_columns_mirrored:+.3f}) over "
          f"{scored} text pages of {len(pages)} rendered "
          f"({len(pages) - scored} carry no boxes to align), every span box holds ink, and "
          f"every rendered page's aspect ratio matches the declared display box")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
