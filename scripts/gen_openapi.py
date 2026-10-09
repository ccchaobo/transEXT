"""Generate the OpenAPI document from the wire contract.

The document is an *output* of the contract, not a parallel description of it
(ADR 0006).  Generating it here, from the same Pydantic models the service will
use, means it cannot drift from the code, and it can be reviewed and diffed
without starting a server.

At M0 the paths are not implemented yet, so this writes the component schemas and
the error responses only.  Paths are added in M1 by the FastAPI application, which
derives them from the models in this same package; the file records that fact
rather than pretending to be a complete API description.
"""

from __future__ import annotations

import argparse
import json
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


def build_document(*, version: str = "0.1.0") -> dict[str, Any]:
    """Build the OpenAPI document for the M0 contract."""
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

    # Error responses are the only part of the API surface that exists at M0, so
    # they are stated as reusable responses rather than left to each path.
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
                "This document is generated from the wire contract (ADR 0006).  At "
                "M0 it describes the component schemas and the error taxonomy; the "
                "path objects are added in M1 by the FastAPI application, which "
                "derives them from the same models.  Its absence of paths is a "
                "statement about what is implemented, not an omission."
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
            "securitySchemes": {
                "loopbackSession": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Session-Token",
                    "description": (
                        "A token minted at start-up and required on every mutating "
                        "request. It protects against a browser page on another "
                        "origin reaching the loopback service. Provider API keys "
                        "are never sent to the client."
                    ),
                }
            },
        },
        "paths": {},
        "x-implementation-status": {
            "milestone": "M0",
            "implemented": [
                "the component schemas below, enforced by tests",
                "the error taxonomy and its HTTP status mapping",
            ],
            "not_yet_implemented": [
                "all path objects (M1)",
                "authentication enforcement (M1)",
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
    args = parser.parse_args(argv)

    document = build_document(version=args.version)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    schemas = document["components"]["schemas"]
    print(f"wrote {args.out}")
    print(f"schemas: {len(schemas)}")
    print(f"paths:   {len(document['paths'])} (added in M1)")
    for name in sorted(schemas):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
