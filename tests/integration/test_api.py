"""The HTTP layer, end to end, against the real corpus and a real database.

This is the acceptance evidence for the API: it imports a real PDF over HTTP, runs the real
worker, and reads the result back through the same endpoints the browser will call.  Nothing is
stubbed except the translation provider, and translation is asserted to *refuse* rather than to
produce something, because there are no credentials in this environment and no mock is allowed
to stand in for a real service call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rr_api import create_app
from rr_services import Settings
from rr_worker import Worker

CORPUS = Path("benchmarks/corpus")
DOCUMENT = "pdf_b_two_column"


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, serve_frontend=False)
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client


def import_and_parse(client) -> str:
    """Import the corpus file and run its parse job.  Returns the document id."""
    source = CORPUS / "files" / f"{DOCUMENT}.pdf"
    with source.open("rb") as stream:
        response = client.post(
            "/api/v1/documents",
            files={"file": (source.name, stream, "application/pdf")},
        )
    assert response.status_code == 201, response.text
    document_id = response.json()["document"]["id"]

    app = client.app
    worker = Worker(app.state.settings, app.state.workspace, app.state.queue, database=app.state.database)
    assert worker.serve(poll_seconds=0, max_jobs=1) == 1
    return document_id


class TestHealth:
    def test_health_reports_that_translation_is_unavailable(self, client) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["translation_available"] is False
        # The key must never be echoed.  The field is a boolean by construction; this asserts
        # that no credential-shaped value appears anywhere in the payload.
        assert "sk-" not in json.dumps(body)

    def test_openapi_describes_the_real_routes(self, client) -> None:
        """The spec's OpenAPI requirement: a document with paths, not an empty one."""
        spec = client.get("/api/openapi.json").json()
        assert spec["paths"], "openapi must not be empty"
        for path in ("/api/v1/documents", "/api/v1/documents/{document_id}",
                     "/api/v1/documents/{document_id}/pages/{page_index}",
                     "/api/v1/jobs/{job_id}", "/api/v1/terms"):
            assert path in spec["paths"], f"{path} missing from openapi"
        assert "/health" in spec["paths"]


class TestImportAndRead:
    def test_a_document_imports_parses_and_reads_back(self, client) -> None:
        document_id = import_and_parse(client)

        detail = client.get(f"/api/v1/documents/{document_id}").json()
        assert detail["parse_status"] == "succeeded"
        assert detail["page_count"] == 2
        assert detail["block_count"] > 40
        assert detail["capabilities"]["readable"] is True
        assert detail["capabilities"]["translation_configured"] is False
        assert detail["source_url"].endswith("/original")
        assert len(detail["revisions"]) == 1
        assert detail["revisions"][0]["is_active"] is True

    def test_a_page_carries_display_space_spans_for_every_block(self, client) -> None:
        document_id = import_and_parse(client)
        page = client.get(f"/api/v1/documents/{document_id}/pages/0").json()

        assert page["page"]["index"] == 0
        assert page["page"]["page_number"] == 1
        assert page["page"]["display_width"] > 0
        assert page["blocks"], "a parsed page must have blocks"

        for block in page["blocks"]:
            assert block["spans"], f"block {block['id']} has no source span"
            for span in block["spans"]:
                assert span["page_index"] >= 0
                x0, y0, x1, y1 = span["bbox"]
                # The frozen contract: normalised, origin top-left, inside the unit square.
                assert 0.0 <= x0 <= x1 <= 1.0, span["bbox"]
                assert 0.0 <= y0 <= y1 <= 1.0, span["bbox"]
            # The block's own union box must contain all of its spans on this page.
            if block["bbox"]:
                bx0, by0, bx1, by1 = block["bbox"]
                for span in block["spans"]:
                    if span["page_index"] != 0:
                        continue
                    assert bx0 <= span["bbox"][0] + 1e-9
                    assert by0 <= span["bbox"][1] + 1e-9
                    assert bx1 >= span["bbox"][2] - 1e-9
                    assert by1 >= span["bbox"][3] - 1e-9

    def test_reading_order_is_the_parsed_order(self, client) -> None:
        document_id = import_and_parse(client)
        page = client.get(f"/api/v1/documents/{document_id}/pages/0").json()
        orders = [block["order"] for block in page["blocks"]]
        assert orders == sorted(orders)
        # The title must come first: this is the defect that the ordering fix addressed, and it
        # is asserted here so a regression in the layout stage fails this test too.
        assert page["blocks"][0]["type"] == "title"

    def test_the_outline_has_real_sections(self, client) -> None:
        document_id = import_and_parse(client)
        body = client.get(f"/api/v1/documents/{document_id}/outline").json()
        assert len(body["outline"]) > 1, "the corpus has numbered sections"
        assert "note" not in body or body["note"] is None
        titles = [entry["title"] for entry in body["outline"]]
        assert any(title.startswith("1.") for title in titles), titles

    def test_an_unknown_document_is_404_not_500(self, client) -> None:
        response = client.get("/api/v1/documents/doc_missing")
        assert response.status_code == 404
        assert response.json()["error_code"] == "not_found"

    def test_a_page_of_an_unparsed_document_is_a_conflict(self, client) -> None:
        source = CORPUS / "files" / f"{DOCUMENT}.pdf"
        with source.open("rb") as stream:
            document_id = client.post(
                "/api/v1/documents", files={"file": (source.name, stream, "application/pdf")}
            ).json()["document"]["id"]
        # The parse job has not been run, so there are no pages yet.  Asking for one is a
        # conflict with the document's state, not a server fault and not a plain 404.
        response = client.get(f"/api/v1/documents/{document_id}/pages/0")
        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == "parse_failed"

    def test_importing_the_same_file_twice_is_idempotent(self, client) -> None:
        source = CORPUS / "files" / f"{DOCUMENT}.pdf"
        with source.open("rb") as stream:
            first = client.post(
                "/api/v1/documents", files={"file": (source.name, stream, "application/pdf")}
            ).json()
        with source.open("rb") as stream:
            second = client.post(
                "/api/v1/documents", files={"file": (source.name, stream, "application/pdf")}
            ).json()
        assert second["duplicate"] is True
        assert second["document"]["id"] == first["document"]["id"]
        assert client.get("/api/v1/documents").json()["total"] == 1

    def test_the_original_is_served_byte_for_byte(self, client) -> None:
        document_id = import_and_parse(client)
        response = client.get(f"/api/v1/documents/{document_id}/original")
        assert response.status_code == 200
        assert response.content == (CORPUS / "files" / f"{DOCUMENT}.pdf").read_bytes()


class TestTranslationRefusal:
    def test_translation_without_credentials_is_refused_and_says_why(self, client) -> None:
        document_id = import_and_parse(client)
        response = client.post(
            f"/api/v1/documents/{document_id}/translate", json={"target_language": "zh"}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "translation_not_configured"
        assert "mock" in detail["message"]
        # Nothing was written: there must be no translation rows claiming success.
        listing = client.get(f"/api/v1/documents/{document_id}/translations").json()
        assert listing["count"] == 0

    def test_the_configuration_endpoint_never_echoes_a_key(self, client) -> None:
        body = client.get("/api/v1/translation/configuration").json()
        assert body["configured"] is False
        assert "api_key" not in body
        assert "sk-" not in json.dumps(body)


class TestJobs:
    def test_a_job_reports_its_lifecycle_and_events(self, client) -> None:
        document_id = import_and_parse(client)
        jobs = client.get(f"/api/v1/jobs?document_id={document_id}").json()["jobs"]
        assert len(jobs) == 1
        job = jobs[0]
        assert job["type"] == "parse"
        assert job["status"] == "succeeded"
        assert job["progress_fraction"] == 1.0
        assert job["total_units"] == 3
        assert job["done_units"] == 3

        events = client.get(f"/api/v1/jobs/{job['id']}/events").json()
        kinds = [event["kind"] for event in events["events"]]
        assert "job_created" in kinds
        assert "job_finished" in kinds
        assert events["last_seq"] >= 1
        # The append-only log must be replayable: asking from the last seq yields nothing new.
        again = client.get(f"/api/v1/jobs/{job['id']}/events?since={events['last_seq']}").json()
        assert again["events"] == []


class TestAnnotationsAndTerms:
    def test_an_annotation_round_trips(self, client) -> None:
        document_id = import_and_parse(client)
        created = client.post(
            f"/api/v1/documents/{document_id}/annotations",
            json={
                "document_id": document_id,
                "kind": "highlight",
                "page_index": 0,
                "bbox": [0.1, 0.2, 0.4, 0.25],
                "selected_text": "Reading Order Recovery",
                "note": "the title",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["kind"] == "highlight"
        assert body["needs_relocation"] is False
        assert body["created_at"]

        listing = client.get(f"/api/v1/documents/{document_id}/annotations").json()
        assert listing["annotations"][0]["id"] == body["id"]
        assert listing["needs_relocation"] == 0

        patched = client.patch(
            f"/api/v1/annotations/{body['id']}", json={"note": "revised"}
        ).json()
        assert patched["note"] == "revised"

        assert client.delete(f"/api/v1/annotations/{body['id']}").status_code == 200
        assert client.get(f"/api/v1/documents/{document_id}/annotations").json()["annotations"] == []

    def test_an_annotation_for_a_different_document_is_refused(self, client) -> None:
        # The path and the body both name a document.  When they disagree the request is
        # refused rather than resolved, because either choice would file the annotation
        # somewhere the caller may not have meant.
        document_id = import_and_parse(client)
        response = client.post(
            f"/api/v1/documents/{document_id}/annotations",
            json={"document_id": "doc_somewhere_else", "page_index": 0, "note": "x"},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == "invalid_request"

    def test_a_term_bumps_its_version_when_the_target_changes(self, client) -> None:
        document_id = import_and_parse(client)
        first = client.post(
            "/api/v1/terms",
            json={"source": "reading order", "target": "阅读顺序"},
            params={"document_id": document_id},
        ).json()
        assert first["version"] == 1
        assert first["scope"] == "document"

        # The version is part of the translation cache key, so a changed term must bump it or
        # cached translations would silently keep the old wording.
        again = client.post(
            "/api/v1/terms",
            json={"source": "reading order", "target": "阅读次序"},
            params={"document_id": document_id},
        ).json()
        assert again["id"] == first["id"]
        assert again["version"] == 2
        assert again["target"] == "阅读次序"
        assert client.get("/api/v1/terms").json()["terms"].__len__() == 1

    def test_a_global_term_has_no_document(self, client) -> None:
        body = client.post(
            "/api/v1/terms",
            json={"source": "layout", "target": "版面", "scope": "global"},
        ).json()
        assert body["scope"] == "global"
        assert body["document_id"] is None


class TestReadingState:
    def test_reading_state_starts_empty_and_round_trips(self, client) -> None:
        document_id = import_and_parse(client)
        assert client.get(f"/api/v1/documents/{document_id}/reading-state").json()["reading_state"] is None

        put = client.put(
            f"/api/v1/documents/{document_id}/reading-state",
            json={"page_index": 1, "view_mode": "stacked", "zoom": 1.5, "link_scrolling": False},
        )
        assert put.status_code == 200, put.text
        assert put.json()["page_index"] == 1
        assert put.json()["view_mode"] == "stacked"

        got = client.get(f"/api/v1/documents/{document_id}/reading-state").json()["reading_state"]
        assert got["page_index"] == 1
        assert got["zoom"] == 1.5
        assert got["link_scrolling"] is False


class TestExport:
    def test_the_export_states_what_it_is(self, client) -> None:
        document_id = import_and_parse(client)
        response = client.get(f"/api/v1/documents/{document_id}/export.md?download=false")
        assert response.status_code == 200
        text = response.text
        assert "reflowed" in text and "not a facsimile" in text
        assert "block** level" in text
        assert "Original sha256" in text
        # With no translations configured, every prose block must be marked, not filled in.
        assert "No translation for this block." in text
        assert "Reading Order Recovery" in text

    def test_a_download_carries_a_filename(self, client) -> None:
        document_id = import_and_parse(client)
        response = client.get(f"/api/v1/documents/{document_id}/export.md")
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["content-disposition"].endswith('.bilingual.md"')


class TestSameOriginGuard:
    def test_a_cross_origin_write_is_refused(self, client) -> None:
        response = client.post(
            "/api/v1/terms",
            json={"source": "x", "target": "y"},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert response.json()["error_code"] == "invalid_request"

    def test_a_loopback_origin_is_accepted(self, client) -> None:
        response = client.post(
            "/api/v1/terms",
            json={"source": "x", "target": "y"},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert response.status_code == 201

    def test_reads_are_never_blocked_by_the_origin_check(self, client) -> None:
        response = client.get("/health", headers={"Origin": "https://evil.example"})
        assert response.status_code == 200


class TestDeletion:
    def test_deletion_requires_confirmation(self, client) -> None:
        document_id = import_and_parse(client)
        response = client.delete(f"/api/v1/documents/{document_id}")
        assert response.status_code == 400
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 200

    def test_deletion_removes_rows_and_files(self, client) -> None:
        document_id = import_and_parse(client)
        original = client.app.state.workspace.original_dir(document_id)
        assert original.exists()

        response = client.delete(f"/api/v1/documents/{document_id}?confirm=true")
        assert response.status_code == 200
        assert response.json()["files_removed"]
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
        # The original must be gone from disk too, or the reader would be told a document was
        # deleted while its text is still there.
        assert not original.exists()

        # And no derived rows may survive it.  The deletion relies on the database's own
        # cascades, which is why the relationships are declared passive; this asserts that the
        # rows are actually gone rather than merely detached from the document.
        from sqlalchemy import func, select

        from rr_storage import Block, Page, ParseRevision, Translation

        database = client.app.state.database
        with database.session() as session:
            for model, name in (
                (Block, "blocks"),
                (Page, "pages"),
                (ParseRevision, "parse_revisions"),
                (Translation, "translations"),
            ):
                remaining = session.scalar(select(func.count()).select_from(model))
                assert remaining == 0, f"{name} still holds {remaining} row(s)"
