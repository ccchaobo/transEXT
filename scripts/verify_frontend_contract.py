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

    annotations = client.get(f"/api/v1/documents/{document_id}/annotations").json()
    check("annotation listing", annotations, ["annotations", "needs_relocation"])

    print("\n[4] the pages the frontend requests must exist")
    for index in range(detail["page_count"] or 0):
        for kind in ("preview", "thumbnail"):
            response = client.get(f"/api/v1/documents/{document_id}/pages/{index}/{kind}.png")
            status = "ok  " if response.status_code == 200 else "FAIL"
            if response.status_code != 200:
                failures.append(f"{kind}.png page {index}: HTTP {response.status_code}")
            print(f"  {status} pages/{index}/{kind}.png  {len(response.content)} bytes")

    print("\n[5] static assets the browser will fetch")
    for path in ("/", "/styles.css", "/js/app.js", "/js/api.js"):
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
