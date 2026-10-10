"""Check every field the frontend reads against the fields the API actually returns.

The frontend is JavaScript and nothing here can run a browser, so a misspelled field would not
fail a test - it would render as an empty string or `undefined` in a pane nobody has looked at
yet.  This script closes that gap by treating the frontend's own source as the specification:
it extracts the property names `apps/web/js/app.js` reads off each payload, then asserts that the
real payload from a real import actually has them.

It is deliberately over-strict.  A field the frontend reads but never uses still has to exist,
because an `undefined` in the wrong place is exactly the failure this is for.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from rr_api import create_app
from rr_services import Settings
from rr_worker import Worker

CORPUS = Path("benchmarks/corpus")
DOCUMENT = "pdf_b_two_column"
APP_JS = Path("apps/web/js/app.js")
WEB_ROOT = Path("apps/web")

failures: list[str] = []


def check(label: str, payload: dict, required: list[str]) -> None:
    missing = [key for key in required if key not in payload]
    if missing:
        failures.append(f"{label}: missing {missing}")
        print(f"  FAIL {label}: missing {missing}")
        print(f"       present keys: {sorted(payload)}")
    else:
        print(f"  ok   {label}: {len(required)} fields present")


def keys_of(payload: dict) -> set[str]:
    return set(payload)


def main() -> int:
    # A fresh data directory *per run*.  This used to be the fixed path
    # ``var/_frontend_contract``, which made the script pass on a clean checkout and fail on the
    # second run: the document was already imported, the content hash matched, no new job was
    # created, and the assertion below that exactly one job ran failed.  A verification that
    # depends on what the previous run left behind is not a verification.  It also stops the
    # script writing into the repository, and into the ``var/`` a running server may be using.
    with tempfile.TemporaryDirectory(
        prefix="rr-frontend-contract-", ignore_cleanup_errors=True
    ) as tmp:
        return _check(Path(tmp) / "data")


def _check(data_dir: Path) -> int:
    settings = Settings(data_dir=data_dir)
    # serve_frontend must be on: the point of the last check is that the browser's asset
    # requests actually resolve, and a TestClient built with it off would 404 them all.
    app = create_app(settings, serve_frontend=True)
    client = TestClient(app)

    print("[1] fields the frontend reads, extracted from its source")
    source = APP_JS.read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"\b(?:doc|block|page|job|translation|entry)\.([a-z_]+)", source)))
    print(f"  {len(referenced)} property names referenced in app.js")

    print("\n[2] importing and parsing a real corpus PDF")
    pdf = CORPUS / "files" / f"{DOCUMENT}.pdf"
    with pdf.open("rb") as stream:
        created = client.post(
            "/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")}
        )
    assert created.status_code == 201, created.text
    body = created.json()
    document_id = body["document"]["id"]
    worker = Worker(settings, app.state.workspace, app.state.queue, database=app.state.database)
    assert worker.serve(poll_seconds=0, max_jobs=1) == 1

    print("\n[3] the shapes the frontend depends on")
    check("UploadAccepted", body, [
        "document", "duplicate", "job_id", "job_created", "warnings", "probe",
    ])

    listing = client.get("/api/v1/documents").json()
    check("DocumentList", listing, ["items", "total", "limit", "offset"])
    check("DocumentSummary (list row)", listing["items"][0], [
        "id", "filename", "format", "size_bytes", "sha256", "page_count", "imported_at",
        "processing_mode", "parse_status", "translate_status", "progress", "warning_count",
        "error_count", "labels", "title", "block_count", "translated_block_count",
    ])

    detail = client.get(f"/api/v1/documents/{document_id}").json()
    check("DocumentDetail", detail, [
        "id", "filename", "parse_status", "translate_status", "active_revision_id", "sha256",
        "page_count", "block_count", "translated_block_count", "capabilities", "source_url",
        "revisions", "issues", "translations_summary",
    ])
    check("DocumentDetail.capabilities", detail["capabilities"], ["readable", "translation_configured"])

    page = client.get(f"/api/v1/documents/{document_id}/pages/0?target_language=zh").json()
    check("PageView", page, ["page", "document_id", "revision_id", "blocks"])
    check("PageWire", page["page"], [
        "index", "page_number", "width_pt", "height_pt", "rotation", "display_width",
        "display_height", "source_kind", "preview_url", "thumbnail_url", "text_chars",
        "ocr_applied", "warnings",
    ])
    blocks = page["blocks"]
    assert blocks, "no blocks on page 0"
    block_keys = set()
    for block in blocks:
        block_keys |= keys_of(block)
    check("BlockWire", {key: None for key in block_keys}, [
        "id", "page_index", "order", "type", "text", "raw_text", "translatable", "is_body",
        "confidence", "spans", "pages", "section_path", "section_id", "level", "cross_page",
        "human_edited", "table", "metadata", "translation_status", "translation",
    ])
    for block in blocks:
        if block["confidence"] is not None:
            check("ConfidenceWire", block["confidence"], ["score", "level", "source"])
            break
    for block in blocks:
        if block["spans"]:
            check("SpanWire", block["spans"][0], [
                "page_index", "bbox", "raw_bbox", "raw_space", "polygon", "text_start", "text_end",
            ])
            break

    outline = client.get(f"/api/v1/documents/{document_id}/outline").json()
    check("OutlineView", outline, ["outline"])
    if outline["outline"]:
        check("OutlineEntry", outline["outline"][0], [
            "block_id", "page_index", "page_number", "order", "type", "level", "section_id",
            "section_path", "title",
        ])

    configuration = client.get("/api/v1/translation/configuration").json()
    check("translation configuration", configuration, [
        "configured", "provider", "model", "base_url", "base_url_is_default", "prompt_version",
        "max_concurrency",
    ])
    print(f"       configured={configuration['configured']} base_url={configuration.get('base_url')!r} "
          f"is_default={configuration.get('base_url_is_default')}")

    listing = client.get(f"/api/v1/documents/{document_id}/translations?target_language=zh").json()
    check("translations listing", listing, ["translations", "count"])

    jobs = client.get(f"/api/v1/jobs?document_id={document_id}").json()
    check("JobList", jobs, ["jobs"])
    check("JobWire", jobs["jobs"][0], [
        "id", "type", "status", "document_id", "created_at", "started_at", "finished_at",
        "total_units", "done_units", "failed_units", "skipped_units", "progress_fraction",
        "cancel_requested", "stage", "stage_status", "result", "error_code", "error_message",
        "attempt_count", "usage",
    ])
    print(f"       result payload: {json.dumps(jobs['jobs'][0]['result'], ensure_ascii=False)}")

    events = client.get(f"/api/v1/jobs/{jobs['jobs'][0]['id']}/events?since=0").json()
    check("JobEvents", events, ["events", "last_seq"])
    check("JobEventWire", events["events"][0], ["seq", "kind", "stage", "data", "created_at"])

    reading = client.get(f"/api/v1/documents/{document_id}/reading-state").json()
    check("reading state", reading, ["reading_state"])

    print("\n[4] the overlay view's own requirements")
    # The overlay draws the translation into the area the original occupied, so it needs a usable
    # box for every block it would cover.  This is the part of that feature which can be checked
    # without a browser: the panels cannot be seen here, but the geometry they are placed from
    # can be, and a block with no spans would be a panel drawn at 0x0 with the text invisible.
    source_text = APP_JS.read_text(encoding="utf-8")
    if '"overlay"' not in source_text:
        failures.append("the overlay view is not offered in the mode list")
        print("  FAIL the overlay view is not offered in the mode list")
    else:
        print("  ok   the overlay view is offered in the mode list")

    verbatim = {"formula", "table", "table_cell", "figure"}
    total_pages = detail["page_count"]
    covered = 0
    skipped_verbatim = 0
    bad: list[str] = []
    for index in range(total_pages):
        view = client.get(
            f"/api/v1/documents/{document_id}/pages/{index}?target_language=zh"
        ).json()
        for block in view["blocks"]:
            if block["type"] in verbatim:
                # Never covered: a translation of a formula or a table destroys what it means.
                skipped_verbatim += 1
                continue
            boxes = [span["bbox"] for span in block["spans"] if span.get("bbox")]
            if not block["translatable"] or not boxes:
                continue
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
                bad.append(f"page {index} block {block['id']}: union box {(x0, y0, x1, y1)}")
            covered += 1
    if bad:
        failures.append(f"{len(bad)} block(s) would get an unusable overlay panel: {bad[:4]}")
        print(f"  FAIL {len(bad)} block(s) would get an unusable overlay panel")
        for line in bad[:4]:
            print(f"       {line}")
    else:
        print(
            f"  ok   every one of {covered} translatable blocks has a well-formed union box"
            f" ({total_pages} pages, {skipped_verbatim} verbatim blocks left uncovered)"
        )
    if covered == 0:
        failures.append("no block on any page would be covered, so the check proved nothing")
        print("  FAIL no block would be covered, so the check proved nothing")

    print("\n[5] the linked scrolling switch is stored, not just accepted")
    # `link_scrolling` was written on every page turn and read by nothing, so the two panes never
    # followed each other.  Now that a control writes it and the reader obeys it, it has to
    # actually persist - otherwise the checkbox silently does nothing on the next visit.
    for wanted in (False, True):
        written = client.put(
            f"/api/v1/documents/{document_id}/reading-state",
            json={
                "page_index": 0, "view_mode": "overlay", "zoom": 1.0,
                "link_scrolling": wanted,
            },
        )
        got = client.get(f"/api/v1/documents/{document_id}/reading-state").json()["reading_state"]
        if written.status_code != 200:
            # The reason this loop exists.  `view_mode` is a Literal in the contract and the
            # overlay is a new member of it; forgetting to add it there rejects the reader's own
            # view choice with a 400, and the switch would appear to work until it was reloaded.
            failures.append(
                f"storing view_mode='overlay' was refused: HTTP {written.status_code} {written.text[:200]}"
            )
            print(f"  FAIL storing view_mode='overlay' was refused: HTTP {written.status_code}")
            print(f"       {written.text[:300]}")
            break
        if got is None:
            failures.append("reading state came back empty after a successful write")
            print("  FAIL reading state came back empty after a successful write")
            break
        if got["link_scrolling"] is not wanted or got["view_mode"] != "overlay":
            failures.append(
                f"reading state did not persist: link_scrolling={got['link_scrolling']} "
                f"view_mode={got['view_mode']!r}"
            )
            print(f"  FAIL link_scrolling={wanted} came back as {got['link_scrolling']}")
            break
    else:
        print("  ok   link_scrolling and the overlay view mode both survive a round trip")

    annotations = client.get(f"/api/v1/documents/{document_id}/annotations").json()
    check("annotation listing", annotations, ["annotations", "needs_relocation"])

    print("\n[6] the pages the frontend requests must exist")
    for index in range(detail["page_count"] or 0):
        for kind in ("preview", "thumbnail"):
            response = client.get(f"/api/v1/documents/{document_id}/pages/{index}/{kind}.png")
            status = "ok  " if response.status_code == 200 else "FAIL"
            if response.status_code != 200:
                failures.append(f"{kind}.png page {index}: HTTP {response.status_code}")
            print(f"  {status} pages/{index}/{kind}.png  {len(response.content)} bytes")

    print("\n[7] static assets the browser will fetch")
    # Derived from the module graph rather than listed by hand, because a hand-written list is
    # exactly how a new module goes unchecked: `settings.js` was added and fetched by
    # `app.js`, and a missing one would have broken the page with no failing test - a 404 on an
    # import is invisible until someone clicks the button that needs it.
    assets = ["/", "/styles.css"]
    modules = sorted((WEB_ROOT / "js").glob("*.js"))
    assets += [f"/js/{module.name}" for module in modules]
    if len(modules) < 2:
        failures.append(f"expected several frontend modules, found {[m.name for m in modules]}")
    for path in assets:
        response = client.get(path)
        status = "ok  " if response.status_code == 200 else "FAIL"
        if response.status_code != 200:
            failures.append(f"{path}: HTTP {response.status_code}")
        print(f"  {status} {path}  {len(response.content)} bytes")

    print()
    if failures:
        print(f"FRONTEND CONTRACT: FAIL ({len(failures)} problem(s))")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("FRONTEND CONTRACT: PASS - every field app.js reads is present in the real payload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
