"""The FastAPI application: one local process serving the API and the reader UI.

Two decisions worth stating, because both are visible in the security posture:

* **Loopback and a same-origin check.**  This is a single-user local tool, so the server binds
  ``127.0.0.1`` and every mutating request must come from the app's own origin.  A page on the
  internet must not be able to make the reader import, translate or delete by posting to
  localhost.  The check is on ``Origin``/``Referer`` because that is what a browser sends; it is
  not an authentication scheme and is not described as one.
* **Secrets stay server-side.**  The translation API key is read from the environment by the
  adapter and is reported to the client only as a boolean.  It is never echoed in a response,
  never sent to the browser, and never written into an export.

The app is created by ``create_app`` so tests can point it at a temporary data directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from rr_contracts import ErrorCode
from rr_services import FileRejected, Settings, open_runtime
from rr_storage import Database, Workspace

from . import reads
from .routes import router

VERSION = "0.1.0"

#: Where the vendored frontend lives.  Served by this process; there is no build step and no
#: second server, so a page refresh is enough to pick up a change.
WEB_ROOT = Path(os.environ.get("RR_WEB_ROOT") or Path(__file__).resolve().parents[3] / "apps" / "web")


def create_app(
    settings: Settings | None = None,
    *,
    workspace: Workspace | None = None,
    database: Database | None = None,
    serve_frontend: bool = True,
) -> FastAPI:
    # One runtime builder, shared with the worker.  Building the database and workspace here and
    # again in the worker is how the two processes come to disagree about which data directory
    # they are using.
    runtime = open_runtime(settings, database=database, workspace=workspace)
    settings = runtime.settings
    database = runtime.database
    workspace = runtime.workspace

    app = FastAPI(
        title="research-reader",
        version=VERSION,
        description=(
            "Local-first bilingual reader for research papers. Originals are immutable; "
            "translations are cached by content, revision, language, provider, model, prompt "
            "and glossary version; failures, OCR uncertainty and partial completion are "
            "reported rather than hidden."
        ),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.database = database
    app.state.workspace = workspace
    app.state.queue = runtime.queue

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        """Refuse a cross-origin state change from a browser."""
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and not _is_local_origin(origin, request):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error_code": ErrorCode.INVALID_REQUEST.value,
                        "message": (
                            "refused a state-changing request from another origin; this "
                            "server accepts requests from its own pages only"
                        ),
                    },
                )
        return await call_next(request)

    @app.exception_handler(FileRejected)
    async def file_rejected(_request: Request, exc: FileRejected):
        # A rejected file is a refusal with a reason, not a server fault.  400 rather than 500,
        # and the code travels so the UI can explain what was wrong with the file.
        return JSONResponse(
            status_code=400,
            content={"error_code": exc.code.value if hasattr(exc.code, "value") else str(exc.code),
                     "message": str(exc)},
        )

    @app.exception_handler(LookupError)
    async def not_found(_request: Request, exc: LookupError):
        return JSONResponse(
            status_code=404,
            content={"error_code": ErrorCode.NOT_FOUND.value, "message": str(exc) or "not found"},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_request: Request, exc: RequestValidationError):
        """A malformed body, in the same shape as every other refusal.

        FastAPI's own handler answers 422 with ``detail`` as a *list* of error objects, while
        every refusal this API raises puts an ``{"error_code", "message"}`` object in
        ``detail``.  The reader maps ``error_code``; given the list it has nothing to map and
        falls back to something generic.  So a body with a mistyped field name - the ordinary
        mistake - would be the one refusal the UI could not explain.
        """
        parts: list[str] = []
        for error in exc.errors():
            # loc is like ("body", "modle"); the first element names the request part and is
            # not useful to a reader.
            location = ".".join(str(item) for item in error.get("loc", ())[1:])
            message = str(error.get("msg") or "invalid value")
            parts.append(f"{location}: {message}" if location else message)
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "error_code": ErrorCode.INVALID_REQUEST.value,
                    "message": "; ".join(parts) or "the request body was not valid",
                }
            },
        )

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        """Liveness plus what this server is actually able to do."""
        from rr_services import resolve_translation_config

        with database.session() as session:
            from sqlalchemy import func, select

            from rr_storage import Block, Document

            documents = session.scalar(select(func.count()).select_from(Document)) or 0
            blocks = session.scalar(select(func.count()).select_from(Block)) or 0
            # Resolved, not merely read from the environment: the reader can configure a key in
            # the settings panel, and health has to report that provider rather than the one the
            # process started with.
            described = resolve_translation_config(session).describe()
        return {
            "status": "ok",
            "version": VERSION,
            "documents": documents,
            "blocks": blocks,
            "data_dir": str(settings.data_dir),
            "translation": described,
            # Stated plainly so nothing downstream can imply otherwise: with no credentials the
            # reader works and translation does not, and no placeholder text is produced.
            "translation_available": described["configured"],
        }

    app.include_router(router)
    from .library import router as library_router
    app.include_router(library_router)
    from .zotero import router as zotero_router
    app.include_router(zotero_router)

    if serve_frontend and WEB_ROOT.is_dir():
        app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")

    return app


def _is_local_origin(origin: str, request: Request) -> bool:
    """True when ``origin`` is this server or another loopback address."""
    from urllib.parse import urlsplit

    host = urlsplit(origin).hostname or ""
    allowed = {"127.0.0.1", "localhost", "::1", "[::1]"}
    if host in allowed:
        return True
    # The Host header is what the client actually used, so a non-loopback Host means the server
    # was reached from elsewhere; in that case only the exact origin is acceptable.
    return host in {urlsplit(f"//{request.headers.get('host', '')}").hostname}


def main() -> None:  # pragma: no cover - exercised by hand, not by tests
    """Run the server on loopback.  ``python -m rr_api`` or ``rr-serve``."""
    import uvicorn

    settings = Settings.from_env()
    env_file = Path(os.environ.get("RR_ENV_FILE") or ".env")
    if env_file.is_file():
        from rr_adapters.translation import load_env_file

        load_env_file(env_file)
        settings = Settings.from_env()

    port = int(os.environ.get("RR_PORT") or 8765)
    # 127.0.0.1, never 0.0.0.0: this is a local tool and binding it to the network would expose
    # the reader, and the API key behind it, to anything on the same network.
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="info")


__all__ = ["VERSION", "WEB_ROOT", "create_app", "main"]
