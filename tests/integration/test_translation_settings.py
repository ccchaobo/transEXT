"""The reader's own way to configure translation, and the worker defect that hid it.

These tests exist because of a specific failure.  The API told the reader, on a 409:

    no translation provider is configured.  Set RR_TRANSLATION_API_KEY (and
    RR_TRANSLATION_BASE_URL if the default endpoint is wrong) and restart the server.

Following that instruction did not work.  ``rr_worker/__main__.py`` built ``Worker(...)``
without a provider, so ``worker.provider`` was ``None`` for the whole run and every translate
job ended in the same ``TranslationNotConfigured`` the reader had already resolved - a second,
identical failure, with nothing to say why.  ``tests/unit/test_worker.py`` never caught it
because it constructs ``Worker`` itself and passes a provider explicitly; only the CLI's own
wiring was wrong, and the CLI is the one thing the unit tests do not build.

The endpoint used for the check call is a **local HTTP test double**, not a remote service and
not a mock provider: the real ``OpenAIChatTranslationProvider`` runs, with a real socket and a
real JSON request, against a server this file starts.  That verifies the wiring and the request
shape.  It is not evidence that any hosted service works, and real-service acceptance stays
PENDING.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from rr_adapters.translation import OpenAITranslationConfig, OpenAIChatTranslationProvider
from rr_api import create_app
from rr_contracts import ErrorCode, JobStatus, JobType
from rr_services import (
    DocumentService,
    Settings,
    provider_from_resolved,
    resolve_translation_config,
)
from rr_storage import Job, JobQueue, Workspace, create_database
from rr_worker import Worker

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks" / "corpus" / "files"
DOCUMENT = "pdf_a_single_column.pdf"

#: A key that is obviously not real, so a leak is unmistakable in an assertion message.
SECRET = "sk-do-not-echo-me-1234567890"


# ---------------------------------------------------------------------------
# a local chat-completions double
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Answers ``/v1/chat/completions`` the way a chat service does, and records requests."""

    reply: dict | None = None
    status: int = 200
    raw: bytes | None = None
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - the base class names it
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "json": json.loads(body) if body else None,
            }
        )
        if type(self).raw is not None:
            payload = type(self).raw
        else:
            payload = json.dumps(
                type(self).reply
                or {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                }
            ).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        """Silence the per-request line; the test's output is the assertion."""


class ChatDouble:
    """A running HTTP server that answers like a chat-completions endpoint."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "ChatDouble":
        _Handler.reply = None
        _Handler.status = 200
        _Handler.raw = None
        _Handler.requests = []
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[dict]:
        return _Handler.requests


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    test_client = TestClient(app)
    test_client.app = app
    return test_client


@pytest.fixture()
def parsed_document(client):
    """Import and parse one real corpus PDF, so a translate request is possible at all."""
    source = CORPUS / DOCUMENT
    with source.open("rb") as stream:
        created = client.post(
            "/api/v1/documents", files={"file": (source.name, stream, "application/pdf")}
        )
    assert created.status_code == 201, created.text
    document_id = created.json()["document"]["id"]
    app = client.app
    worker = Worker(
        app.state.settings, app.state.workspace, app.state.queue, database=app.state.database
    )
    worker.serve(poll_seconds=0, max_jobs=1, exit_when_idle=True, idle_grace=0.0)
    return document_id


def configure(client, **values) -> dict:
    response = client.put("/api/v1/settings/translation", json=values)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# the settings endpoint
# ---------------------------------------------------------------------------


class TestThePanelCanBeRead:
    def test_a_fresh_install_says_nothing_is_configured(self, client) -> None:
        body = client.get("/api/v1/settings/translation").json()
        assert body["effective"]["configured"] is False
        assert body["effective"]["has_api_key"] is False
        assert body["stored"] == {}
        assert body["env_overrides"] == []

    def test_it_names_the_fields_the_reader_may_set(self, client) -> None:
        body = client.get("/api/v1/settings/translation").json()
        fields = {entry["field"] for entry in body["fields"]}
        assert {"base_url", "api_key", "model"} <= fields
        secret = {entry["field"] for entry in body["fields"] if entry["secret"]}
        assert secret == {"api_key"}

    def test_the_default_endpoint_is_flagged_as_a_default(self, client) -> None:
        """The Ollama cloud URL could not be verified here, so it must not look authoritative."""
        body = client.get("/api/v1/settings/translation").json()
        assert body["effective"]["base_url_is_default"] is True
        assert body["effective"]["base_url"].startswith("http")


class TestStoringSettings:
    def test_a_key_and_a_model_make_translation_available(self, client, parsed_document) -> None:
        before = client.post(f"/api/v1/documents/{parsed_document}/translate", json={})
        assert before.status_code == 409
        assert before.json()["detail"]["error_code"] == ErrorCode.TRANSLATION_NOT_CONFIGURED.value

        configure(client, api_key=SECRET, model="some-model")

        after = client.post(f"/api/v1/documents/{parsed_document}/translate", json={})
        assert after.status_code == 202, after.text

    def test_the_key_is_never_echoed_by_any_endpoint(self, client, parsed_document) -> None:
        """Every response a browser can reach is scanned, not just the settings one."""
        configure(client, api_key=SECRET, model="some-model")
        paths = [
            "/health",
            "/api/v1/settings/translation",
            "/api/v1/translation/configuration",
            "/api/v1/documents",
            f"/api/v1/documents/{parsed_document}",
        ]
        for path in paths:
            body = client.get(path).text
            assert SECRET not in body, f"{path} echoed the API key"
        # And the key really is stored, so the test above is not passing on an empty store.
        assert client.get("/api/v1/settings/translation").json()["effective"]["has_api_key"] is True

    def test_each_value_reports_where_it_came_from(self, client) -> None:
        configure(client, base_url="http://example.test/v1", model="m1", api_key=SECRET)
        sources = client.get("/api/v1/settings/translation").json()["effective"]["sources"]
        assert sources["base_url"] == "settings"
        assert sources["model"] == "settings"
        assert sources["api_key"] == "settings"

    def test_the_stored_value_is_reported_separately_from_the_effective_one(self, client) -> None:
        configure(client, model="typed-in-the-panel")
        body = client.get("/api/v1/settings/translation").json()
        assert body["stored"]["model"] == "typed-in-the-panel"
        assert body["effective"]["model"] == "typed-in-the-panel"

    def test_a_partial_update_leaves_the_other_fields_alone(self, client) -> None:
        configure(client, api_key=SECRET, model="m1", base_url="http://example.test/v1")
        configure(client, model="m2")
        body = client.get("/api/v1/settings/translation").json()
        assert body["effective"]["model"] == "m2"
        assert body["effective"]["has_api_key"] is True
        assert body["effective"]["base_url"] == "http://example.test/v1"

    def test_an_empty_string_clears_a_field(self, client) -> None:
        configure(client, api_key=SECRET, model="m1")
        configure(client, api_key="")
        body = client.get("/api/v1/settings/translation").json()
        assert body["effective"]["has_api_key"] is False
        assert body["effective"]["configured"] is False

    def test_an_unknown_field_is_refused_rather_than_stored(self, client) -> None:
        """A typo that appears to save is worse than an error."""
        response = client.put("/api/v1/settings/translation", json={"modle": "m1"})
        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST.value
        assert client.get("/api/v1/settings/translation").json()["stored"] == {}

    def test_delete_forgets_everything_stored(self, client) -> None:
        configure(client, api_key=SECRET, model="m1")
        body = client.delete("/api/v1/settings/translation").json()
        assert body["removed"] == 2
        assert client.get("/api/v1/settings/translation").json()["stored"] == {}


class TestTheEnvironmentStillWins:
    def test_an_environment_variable_overrides_a_stored_setting(self, client, monkeypatch) -> None:
        configure(client, model="stored-model", api_key=SECRET)
        monkeypatch.setenv("RR_TRANSLATION_MODEL", "env-model")
        body = client.get("/api/v1/settings/translation").json()
        assert body["effective"]["model"] == "env-model"
        assert body["effective"]["sources"]["model"] == "env"

    def test_the_reason_is_named_rather_than_left_to_be_guessed(self, client, monkeypatch) -> None:
        """Without this the panel looks broken: the reader saves a value and sees another."""
        configure(client, model="stored-model")
        monkeypatch.setenv("RR_TRANSLATION_MODEL", "env-model")
        body = client.get("/api/v1/settings/translation").json()
        assert body["env_overrides"] == ["model"]
        # The value the reader typed is still visible, next to the one actually in force.
        assert body["stored"]["model"] == "stored-model"
        assert body["effective"]["model"] == "env-model"


class TestRefusalsNameTheFieldThatIsMissing:
    def test_a_key_without_a_model_says_so(self, client, parsed_document) -> None:
        configure(client, api_key=SECRET)
        response = client.post(f"/api/v1/documents/{parsed_document}/translate", json={})
        assert response.status_code == 409
        message = response.json()["detail"]["message"]
        assert "model" in message.lower()
        assert "mock" in message

    def test_nothing_at_all_says_the_key_is_missing(self, client, parsed_document) -> None:
        response = client.post(f"/api/v1/documents/{parsed_document}/translate", json={})
        assert "API key" in response.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# the check call
# ---------------------------------------------------------------------------


class TestTheCheckCall:
    def test_it_refuses_when_there_is_no_key_to_test(self, client) -> None:
        response = client.post("/api/v1/settings/translation/test")
        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == ErrorCode.TRANSLATION_NOT_CONFIGURED.value

    def test_it_says_a_model_is_missing_when_only_a_key_is_set(self, client) -> None:
        configure(client, api_key=SECRET)
        response = client.post("/api/v1/settings/translation/test")
        assert response.status_code == 409
        assert "model" in response.json()["detail"]["message"].lower()

    def test_a_working_endpoint_is_reported_with_its_latency_and_a_sample(self, client) -> None:
        with ChatDouble() as double:
            configure(client, api_key=SECRET, model="test-model", base_url=double.base_url)
            body = client.post("/api/v1/settings/translation/test").json()
            assert body["ok"] is True, body
            assert body["model"] == "test-model"
            assert body["latency_ms"] is not None and body["latency_ms"] >= 0
            assert body["sample"] == "ok"
            assert body["endpoint"].endswith("/chat/completions")
            assert body["outbound_request"] is True
            # Nothing is stored by a check: a check that wrote a translation would poison the
            # cache with a fake unit.
            calls = double.requests
            assert len(calls) == 1
            assert calls[0]["path"] == "/v1/chat/completions"
            assert calls[0]["authorization"] == f"Bearer {SECRET}"

    def test_a_wrong_base_url_is_diagnosed_rather_than_reported_as_a_failure(self, client) -> None:
        """The most likely mistake with an unverified endpoint is a wrong path, and the
        provider must say which mistake it was."""
        with ChatDouble() as double:
            _Handler.raw = b"<html><body>404 Not Found</body></html>"
            configure(client, api_key=SECRET, model="test-model", base_url=double.base_url)
            body = client.post("/api/v1/settings/translation/test").json()
            assert body["ok"] is False
            assert body["http_status"] == 200  # the double answered; the body was not JSON
            assert body["error_code"] == ErrorCode.TRANSLATION_FAILED.value
            assert "base URL" in body["error_message"]

    def test_an_authentication_failure_is_named_as_an_authentication_failure(self, client) -> None:
        with ChatDouble() as double:
            _Handler.status = 401
            _Handler.raw = b'{"error":"invalid api key"}'
            configure(client, api_key=SECRET, model="test-model", base_url=double.base_url)
            body = client.post("/api/v1/settings/translation/test").json()
            assert body["ok"] is False
            assert body["error_code"] == ErrorCode.EXTERNAL_AUTH_FAILED.value

    def test_the_check_does_not_retry(self, client) -> None:
        """A diagnostic that sits through three backoffs before answering is a worse one."""
        with ChatDouble() as double:
            _Handler.status = 503
            _Handler.raw = b'{"error":"unavailable"}'
            configure(client, api_key=SECRET, model="test-model", base_url=double.base_url)
            client.post("/api/v1/settings/translation/test")
            assert len(double.requests) == 1


# ---------------------------------------------------------------------------
# the worker defect
# ---------------------------------------------------------------------------


class TestTheWorkerResolvesAProvider:
    """The regression these tests exist for.

    The unit tests pass a provider straight into ``Worker``, which is exactly what the CLI
    failed to do.  These build the worker the way the CLI does, from a factory, so the
    missing wire cannot come back unnoticed.
    """

    @pytest.fixture()
    def harness(self, tmp_path):
        settings = Settings(data_dir=tmp_path / "data")
        database = create_database(settings.data_dir)
        database.create_all()
        workspace = Workspace(settings.workspace_root)
        queue = JobQueue(lease_seconds=120)
        service = DocumentService(settings, workspace, queue)
        return settings, database, workspace, queue, service

    def _import_and_parse(self, harness) -> str:
        """Import a real PDF and run the parse job, without any provider involved."""
        settings, database, workspace, queue, service = harness
        source = CORPUS / DOCUMENT
        with database.session() as session:
            with source.open("rb") as stream:
                result = service.import_file(session, filename=source.name, stream=stream)
            session.commit()
            document_id = result.document.id
        Worker(settings, workspace, queue, database=database).serve(
            poll_seconds=0, max_jobs=2, exit_when_idle=True, idle_grace=0.0
        )
        return document_id

    def _cli_style_provider(self, database):
        """Copied from ``rr_worker/__main__._provider``: the CLI's own wiring."""
        with database.session() as session:
            resolved = resolve_translation_config(session)
        try:
            return provider_from_resolved(resolved)
        except Exception:  # noqa: BLE001
            return None

    def test_a_translate_job_succeeds_from_a_factory_alone(self, harness) -> None:
        """Before the fix, ``provider`` was None and this job always failed."""
        settings, database, workspace, queue, _ = harness
        document_id = self._import_and_parse(harness)

        with database.session() as session:
            job, _created = queue.enqueue(
                session,
                job_type=JobType.TRANSLATE.value,
                document_id=document_id,
                request={"target_language": "zh", "source_language": "en"},
            )
            session.commit()
            job_id = job.id

        with ChatDouble() as double:
            with database.session() as session:
                from rr_services import write_translation_settings

                write_translation_settings(
                    session,
                    {"api_key": SECRET, "model": "test-model", "base_url": double.base_url},
                )
            worker = Worker(
                settings,
                workspace,
                queue,
                database=database,
                provider_factory=lambda: self._cli_style_provider(database),
            )
            with database.session() as session:
                result = worker.run_once(session)

            assert result is not None, "no job was claimed"
            assert result.status in {
                JobStatus.SUCCEEDED.value,
                JobStatus.PARTIALLY_SUCCEEDED.value,
            }, result.detail
            assert double.requests, "the provider never reached the configured endpoint"

        with database.session() as session:
            row = session.get(Job, job_id)
            assert row.error_code is None
            assert row.error_message is None

    def test_without_configuration_the_job_still_fails_and_says_why(self, harness) -> None:
        """The refusal must survive: an unconfigured install must not silently succeed."""
        settings, database, workspace, queue, _ = harness
        document_id = self._import_and_parse(harness)
        with database.session() as session:
            queue.enqueue(
                session,
                job_type=JobType.TRANSLATE.value,
                document_id=document_id,
                request={"target_language": "zh", "source_language": "en"},
            )
            session.commit()
        worker = Worker(
            settings,
            workspace,
            queue,
            database=database,
            provider_factory=lambda: self._cli_style_provider(database),
        )
        with database.session() as session:
            result = worker.run_once(session)
        assert result is not None
        assert result.status == JobStatus.FAILED.value
        with database.session() as session:
            row = session.scalars(
                select(Job).where(Job.type == JobType.TRANSLATE.value)
            ).first()
            assert row.error_code == ErrorCode.TRANSLATION_NOT_CONFIGURED.value

    def test_a_fixed_provider_still_wins_over_the_factory(self, harness) -> None:
        """Tests inject a stub and must keep getting that stub, not a network call."""
        settings, database, workspace, queue, _ = harness

        class Marker:
            name = "marker"
            model = "marker-model"

        worker = Worker(
            settings,
            workspace,
            queue,
            database=database,
            provider=Marker(),
            provider_factory=lambda: (_ for _ in ()).throw(AssertionError("factory called")),
        )
        assert worker.resolve_provider().name == "marker"

    def test_with_neither_there_is_no_provider(self, harness) -> None:
        settings, database, workspace, queue, _ = harness
        worker = Worker(settings, workspace, queue, database=database)
        assert worker.resolve_provider() is None
