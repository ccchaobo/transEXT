"""Live acceptance: start the real server and the real worker, then talk to them over HTTP.

Everything in this script goes through a socket.  That is the point: the integration tests use
``TestClient``, which calls the application in-process and therefore cannot catch a fault in the
parts that only exist when a server is running - that the frontend is mounted at the right path,
that the worker is a separate process that can see the same database, that a refusal survives the
trip as a status code and a JSON body, that the page image is a real PNG on disk.

Four defects were found by doing this by hand before it was automated:

* a document whose parse had failed returned **500** from its own detail endpoint, because a
  comprehension's filter was meant to guard its iterable and does not;
* a parse failure was recorded as ``translation_failed``, sending the reader to check an API key;
* a document kept ``parse_status = "pending"`` forever after its parse had failed, so a reader
  waited for work that had already finished badly;
* translating a document that does not exist answered ``translation_not_configured``, because
  the provider check ran before the document lookup.

Run: ``python scripts/verify_live_acceptance.py``
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "benchmarks" / "corpus"
PYTHON = sys.executable

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    return bool(ok)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Live:
    """The API and a worker, running as real processes against one data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {
            **os.environ,
            "RR_DATA_DIR": str(data_dir),
            "RR_PORT": str(self.port),
            "PYTHONIOENCODING": "utf-8",
        }
        self.api: subprocess.Popen | None = None

    def __enter__(self) -> "Live":
        # stdout/stderr go to DEVNULL rather than pipes: nothing reads them, and a pipe nobody
        # drains can fill and block the child.
        self.api = subprocess.Popen(
            [PYTHON, "-m", "rr_api"],
            cwd=REPO,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.base}/health", timeout=2.0).status_code == 200:
                    return self
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.4)
        raise RuntimeError(f"the API did not answer on {self.base} within 60s")

    def __exit__(self, *exc) -> None:
        if self.api is not None:
            self.api.terminate()
            with contextlib.suppress(Exception):
                self.api.wait(timeout=15)
            if self.api.poll() is None:
                self.api.kill()

    def worker(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, "-m", "rr_worker", *args],
            cwd=REPO,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=600,
        )

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base, timeout=120.0)


def upload(client: httpx.Client, path: Path, content_type: str = "") -> httpx.Response:
    with path.open("rb") as stream:
        return client.post(
            "/api/v1/documents",
            files={"file": (path.name, stream, content_type or "application/octet-stream")},
        )


def png_size(payload: bytes) -> tuple[int, int]:
    """Width and height out of a PNG's IHDR, without an image library."""
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    return width, height


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rr-live-", ignore_cleanup_errors=True) as tmp:
        with Live(Path(tmp) / "data") as live, live.client() as client:
            run_checks(live, client)

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print()
    for ok, name, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


def run_checks(live: Live, client: httpx.Client) -> None:
    # ---- the reader is served ------------------------------------------------------------
    health = client.get("/health")
    check("GET /health answers ok", health.status_code == 200 and health.json()["status"] == "ok",
          f"HTTP {health.status_code}")

    shell = client.get("/")
    check("GET / serves the reader shell",
          shell.status_code == 200 and "/js/app.js" in shell.text,
          f"HTTP {shell.status_code}, {len(shell.text)} bytes")

    for asset in ("/styles.css", "/js/app.js", "/js/api.js"):
        response = client.get(asset)
        check(f"GET {asset} is served",
              response.status_code == 200 and len(response.content) > 500,
              f"HTTP {response.status_code}, {len(response.content)} bytes")

    traversal = client.get("/../pyproject.toml")
    check("a path traversal is refused", traversal.status_code in {400, 403, 404},
          f"HTTP {traversal.status_code}")

    # ---- import and parse a real text PDF ------------------------------------------------
    pdf = CORPUS / "files" / "pdf_b_two_column.pdf"
    if not pdf.is_file():
        check("the corpus exists", False, f"{pdf} is missing")
        return

    created = upload(client, pdf, "application/pdf")
    check("a text PDF is accepted", created.status_code == 201, f"HTTP {created.status_code}")
    if created.status_code != 201:
        return
    document_id = created.json()["document"]["id"]
    check("import queues a parse job", bool(created.json().get("job_id")),
          f"job_id={created.json().get('job_id')}")

    finished = live.worker("--once", "--quiet")
    check("the worker process runs the job and exits",
          finished.returncode == 0, f"exit {finished.returncode}")

    detail = client.get(f"/api/v1/documents/{document_id}").json()
    check("the parse succeeded", detail["parse_status"] == "succeeded", detail["parse_status"])
    check("the document reports its pages and blocks",
          detail["page_count"] == 2 and detail["block_count"] > 0,
          f"pages={detail['page_count']} blocks={detail['block_count']}")
    check("the document is readable and translatable",
          detail["capabilities"]["readable"] and detail["capabilities"]["translatable"],
          json.dumps(detail["capabilities"]))

    # ---- the page, its overlay boxes and its rendered image ------------------------------
    page = client.get(f"/api/v1/documents/{document_id}/pages/0").json()
    blocks = page["blocks"]
    check("page 1 has blocks in reading order",
          len(blocks) > 0 and [b["order"] for b in blocks] == list(range(len(blocks))),
          f"{len(blocks)} blocks")
    if blocks:
        check("the title is read before the body",
              blocks[0]["type"] == "title", f"first block type={blocks[0]['type']}")
        boxes = [span["bbox"] for block in blocks for span in block["spans"]]
        in_range = all(0.0 <= v <= 1.0 for box in boxes for v in box)
        check("every span box is inside the page", in_range and len(boxes) > 0,
              f"{len(boxes)} spans")
        check("every block carries spans",
              all(block["spans"] for block in blocks), f"{len(blocks)} blocks")

    preview = client.get(f"/api/v1/documents/{document_id}/pages/0/preview.png")
    check("the page image is a PNG", preview.status_code == 200 and preview.content[:8] ==
          b"\x89PNG\r\n\x1a\n", f"HTTP {preview.status_code}, {len(preview.content)} bytes")
    if preview.status_code == 200:
        width, height = png_size(preview.content)
        expected = page["page"]["display_width"] / page["page"]["display_height"]
        check("the image agrees with the page's aspect ratio",
              abs((width / height) - expected) < 0.02,
              f"image {width}x{height} vs page {expected:.4f}")
    thumbnail = client.get(f"/api/v1/documents/{document_id}/pages/0/thumbnail.png")
    check("the thumbnail is served", thumbnail.status_code == 200, f"HTTP {thumbnail.status_code}")

    missing_page = client.get(f"/api/v1/documents/{document_id}/pages/99")
    check("a page that does not exist is 404", missing_page.status_code == 404,
          f"HTTP {missing_page.status_code}")

    # ---- outline and export --------------------------------------------------------------
    outline = client.get(f"/api/v1/documents/{document_id}/outline").json()
    check("the outline is derived from headings", len(outline["outline"]) >= 2,
          f"{len(outline['outline'])} entries")
    levels = {entry["level"] for entry in outline["outline"]}
    check("the outline has more than one level", len(levels) > 1, f"levels={sorted(levels)}")

    export = client.get(f"/api/v1/documents/{document_id}/export.md?download=false")
    text = export.text
    check("the Markdown export is produced", export.status_code == 200 and len(text) > 1000,
          f"HTTP {export.status_code}, {len(text)} bytes")
    check("the export states that it is reflowed, not a facsimile",
          "reflowed" in text and "not a facsimile" in text, "")
    check("the export states the anchoring level",
          "anchored at **block** level" in text, "")

    # ---- refusal semantics ---------------------------------------------------------------
    body = {"scope": "document", "target_language": "zh"}
    no_provider = client.post(f"/api/v1/documents/{document_id}/translate", json=body)
    check("translation without a provider is refused as not-configured",
          no_provider.status_code == 409 and
          no_provider.json()["detail"]["error_code"] == "translation_not_configured",
          f"HTTP {no_provider.status_code}")
    check("the refusal says no mock is produced",
          "mock" in no_provider.json()["detail"]["message"], "")

    ghost = client.post("/api/v1/documents/doc_does_not_exist/translate", json=body)
    check("a missing document is 404, not a missing API key",
          ghost.status_code == 404 and ghost.json()["error_code"] == "not_found",
          f"HTTP {ghost.status_code}")

    foreign = client.post(
        "/api/v1/documents", headers={"Origin": "https://example.invalid"}, json={}
    )
    check("a state change from another origin is refused",
          foreign.status_code == 403, f"HTTP {foreign.status_code}")

    # ---- a DOCX, which is refused at the parse stage -------------------------------------
    docx = CORPUS / "files" / "docx_paper_a.docx"
    if docx.is_file():
        created = upload(client, docx)
        if created.status_code == 201:
            id2 = created.json()["document"]["id"]
            live.worker("--once", "--quiet")
            detail2 = client.get(f"/api/v1/documents/{id2}")
            check("a document whose parse failed can still be read",
                  detail2.status_code == 200, f"HTTP {detail2.status_code}")
            body2 = detail2.json()
            check("it says its parse failed rather than staying pending",
                  body2["parse_status"] == "failed", body2["parse_status"])
            jobs = client.get(f"/api/v1/jobs?document_id={id2}").json()["jobs"]
            check("the failure is coded as an unsupported format",
                  jobs[0]["error_code"] == "unsupported_format", jobs[0]["error_code"])
        else:
            check("the DOCX is either parsed or refused, never silently dropped", False,
                  f"HTTP {created.status_code}")

    # ---- a scanned PDF, which parses into pages but no text ------------------------------
    scan = CORPUS / "files" / "scan_a_clean_300dpi.pdf"
    if scan.is_file():
        created = upload(client, scan, "application/pdf")
        if created.status_code == 201:
            id3 = created.json()["document"]["id"]
            live.worker("--once", "--quiet")
            detail3 = client.get(f"/api/v1/documents/{id3}").json()
            check("a scanned document parses but has nothing to translate",
                  detail3["parse_status"] == "succeeded" and
                  detail3["capabilities"]["readable"] is True and
                  detail3["capabilities"]["translatable"] is False,
                  f"status={detail3['parse_status']} "
                  f"readable={detail3['capabilities']['readable']} "
                  f"translatable={detail3['capabilities']['translatable']}")
            check("its warnings are visible without opening it",
                  detail3["warning_count"] > 0, f"warning_count={detail3['warning_count']}")
            scanned_page = client.get(f"/api/v1/documents/{id3}/pages/0").json()["page"]
            warnings = " ".join(scanned_page.get("warnings") or [])
            check("the page explains that OCR is required",
                  "OCR" in warnings, warnings[:120])
            check("the page reports that OCR was not applied",
                  scanned_page.get("ocr_applied") is False, "")
            image = client.get(f"/api/v1/documents/{id3}/pages/0/preview.png")
            check("a scanned page is still shown as an image",
                  image.status_code == 200 and len(image.content) > 10_000,
                  f"{len(image.content)} bytes")
        else:
            check("a scanned PDF is accepted", False, f"HTTP {created.status_code}")


    # ---- the settings panel, over real HTTP -----------------------------------------------
    # Deliberately the **last** section, and that placement is load-bearing.  Storing settings
    # makes the translate endpoint succeed, so the check below queues a real translation job -
    # and the sections after this one run the worker as `--once`, which means exactly one job.
    # An earlier revision of this block sat above them and the leftover job was claimed instead
    # of the parse job each of them was waiting for, so a scanned PDF appeared to have no pages.
    # The same failure the harness exists to catch: state left by one step breaking the next.
    settings = client.get("/api/v1/settings/translation")
    check("the translation settings are readable",
          settings.status_code == 200, f"HTTP {settings.status_code}")
    panel = settings.json()
    check("the panel reports the effective value and the stored one separately",
          "effective" in panel and "stored" in panel and "fields" in panel, "")
    check("it names the fields that may be set",
          {entry["field"] for entry in panel["fields"]} >= {"base_url", "api_key", "model"}, "")
    check("nothing stored yet, and the environment is not overriding anything",
          panel["stored"] == {} and panel["env_overrides"] == [])


    secret = "sk-live-acceptance-must-not-echo"
    untestable = client.post("/api/v1/settings/translation/test")
    check("the check call refuses when there is no key to test",
          untestable.status_code == 409 and
          untestable.json()["detail"]["error_code"] == "translation_not_configured",
          f"HTTP {untestable.status_code}")

    stored = client.put(
        "/api/v1/settings/translation",
        json={"api_key": secret, "model": "live-check-model", "base_url": "http://127.0.0.1:9/v1"},
    )
    check("a reader can store a key, a model and an endpoint", stored.status_code == 200,
          f"HTTP {stored.status_code}")

    # The key travels one way.  Check every response a browser can reach, not just this one.
    leaked = [
        path for path in (
            "/health",
            "/api/v1/settings/translation",
            "/api/v1/translation/configuration",
            "/api/v1/documents",
            f"/api/v1/documents/{document_id}",
        )
        if secret in client.get(path).text
    ]
    check("the key is stored but never echoed by any endpoint", leaked == [], f"leaked via {leaked}")

    check("health now reports translation as available",
          client.get("/health").json()["translation_available"] is True, "")
    check("the document view agrees that translation is configured",
          client.get(f"/api/v1/documents/{document_id}").json()["capabilities"][
              "translation_configured"
          ] is True, "")
    check("the same settings are now usable, so the 409 is gone",
          client.post(f"/api/v1/documents/{document_id}/translate", json=body).status_code == 202, "")

    typo = client.put("/api/v1/settings/translation", json={"modle": "m1"})
    check("a mistyped field name is refused rather than silently dropped",
          typo.status_code == 400 and
          typo.json()["detail"]["error_code"] == "invalid_request",
          f"HTTP {typo.status_code}")

    cleared = client.delete("/api/v1/settings/translation")
    check("clearing the panel settings restores the refusal",
          cleared.status_code == 200 and
          client.post(f"/api/v1/documents/{document_id}/translate", json=body).status_code == 409,
          f"HTTP {cleared.status_code}")



if __name__ == "__main__":
    sys.exit(main())
