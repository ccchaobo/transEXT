"""Generate the OpenAPI document from the wire contract and the real application.

The document is an *output* of the contract, not a parallel description of it
(ADR 0006).  Component schemas are generated here, from the same Pydantic models the
service uses, so they cannot drift from the code and can be reviewed and diffed
without starting a server.

Path objects are taken from the FastAPI application itself, which declares the
contract models as its request and response models.  Until the API existed this file
recorded ``paths: {}`` and said so in its own metadata; now it carries the real
routes, and the "no paths yet" note is gone rather than left to become a lie.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parent.parent

__all__ = ["build_document", "main"]


def _collect_models() -> dict[str, Any]:
    """Every Pydantic model in the contract package, keyed by class name."""
    import pydantic

    import rr_contracts

    found: dict[str, Any] = {}
    for name in dir(rr_contracts):
        value = getattr(rr_contracts, name)
        if (
            isinstance(value, type)
            and issubclass(value, pydantic.BaseModel)
            and value is not pydantic.BaseModel
        ):
            found[name] = value
    return found


def _app_paths() -> tuple[dict[str, Any], dict[str, Any]]:
    """The paths and schemas the running application publishes.

    The app is built against a temporary data directory.  Using the default one would create a
    real database and workspace as a side effect of generating documentation, which is not
    something a doc generator should do to the user's data.
    """
    from rr_api import create_app
    from rr_services import Settings

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        app = create_app(Settings(data_dir=Path(scratch) / "data"), serve_frontend=False)
        document = app.openapi()
    return document.get("paths", {}), document.get("components", {}).get("schemas", {})


def build_document(*, version: str = "0.1.0", with_paths: bool = True) -> dict[str, Any]:
    """Build the OpenAPI document for the implemented API."""
    models = _collect_models()

    schemas: dict[str, Any] = {}
    for name, model in sorted(models.items()):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for nested_name, nested in (schema.pop("$defs", {}) or {}).items():
            nested.setdefault("title", nested_name)
            # A promoted definition must not clobber a real model of the same
            # name: the real model's schema is authoritative, so `setdefault` is
            # used in both directions and the two passes agree.
            schemas.setdefault(nested_name, nested)
        schema.setdefault("title", name)
        schemas[name] = schema

    paths: dict[str, Any] = {}
    if with_paths:
        paths, app_schemas = _app_paths()
        # The contract's own schemas stay authoritative; anything the app generated that the
        # contract does not name (its wrapper models) is added.  A disagreement is visible as a
        # contract schema that differs from the app's, which is what a test should catch.
        for name, schema in app_schemas.items():
            schemas.setdefault(name, schema)

    # Error responses are the only part of the API surface that is stated as a reusable response
    # rather than repeated on each path.
    error_response = {
        "description": "A business error, with a machine-readable code.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Research Reader API",
            "version": version,
            "description": (
                "Local-first bilingual research-paper reader.\n\n"
                "This document is generated from the wire contract (ADR 0006) and the FastAPI "
                "application, so the schemas cannot drift from the code.\n\n"
                "There is no authentication. The service binds loopback and refuses a "
                "state-changing request whose Origin or Referer is not local, which stops a page "
                "on another origin from making this service import, translate or delete. That is "
                "a browser-origin check and is not described as authentication."
            ),
        },
        "servers": [
            {
                "url": "http://127.0.0.1:8765",
                "description": "Default loopback binding. The service does not listen on a public interface by default.",
            }
        ],
        "components": {
            "schemas": schemas,
            "responses": {
                "BusinessError": error_response,
            },
            # No securitySchemes.  An earlier revision of this file declared an
            # `X-Session-Token` scheme that the implementation never had, which is worse than
            # having none: it would tell a client to send a header that does nothing.
        },
        "paths": paths,
        "x-implementation-status": {
            "milestone": "M1",
            "implemented": [
                "the component schemas below, enforced by tests",
                "the error taxonomy and its HTTP status mapping",
                f"{len(paths)} path objects, served by the FastAPI application",
                "import, parse, page images, outline, annotations, terms, reading state, jobs "
                "and the bilingual Markdown export",
            ],
            "not_yet_implemented": [
                "real-service translation acceptance (no provider credentials in this environment)",
                "OCR wiring into the parse stage",
                "DOCX conversion wiring into the parse stage",
            ],
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the OpenAPI document")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "packages" / "contracts" / "openapi.json",
    )
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument(
        "--schemas-only",
        action="store_true",
        help="omit path objects (the M0 shape, kept so the contract can be inspected alone)",
    )
    args = parser.parse_args(argv)

    document = build_document(version=args.version, with_paths=not args.schemas_only)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    schemas = document["components"]["schemas"]
    print(f"wrote {args.out}")
    print(f"schemas: {len(schemas)}")
    print(f"paths:   {len(document['paths'])}")
    for path in sorted(document["paths"]):
        methods = ",".join(sorted(m.upper() for m in document["paths"][path]))
        print(f"  {methods:<28} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
