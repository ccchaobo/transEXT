"""Check the generated OpenAPI document for internal consistency.

A document with a dangling ``$ref`` is worse than no document: a client generator
will fail with a confusing error, or silently drop the field.  These checks are
run by the test suite as well as by hand, because the document is generated and a
generation change could break a reference without touching any endpoint.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parent.parent
DEFAULT = REPO / "packages" / "contracts" / "openapi.json"


def collect_refs(node: Any, found: set[str]) -> None:
    """Every ``$ref`` string anywhere in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value)
            else:
                collect_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, found)


def check(document: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty means the document is consistent."""
    problems: list[str] = []
    schemas = document.get("components", {}).get("schemas", {})

    if not schemas:
        problems.append("no component schemas were generated")

    refs: set[str] = set()
    collect_refs(document, refs)
    known = {f"#/components/schemas/{name}" for name in schemas}
    for ref in sorted(refs - known):
        problems.append(f"dangling $ref: {ref}")

    for name, schema in sorted(schemas.items()):
        if not isinstance(schema, dict):
            problems.append(f"{name}: schema is not an object")
            continue
        if "title" not in schema:
            problems.append(f"{name}: no title, so a client generator cannot name it")
        # Every schema must be reachable as a component, which is what the name
        # collision handling in the generator exists to guarantee.
        if schema.get("title") not in (None, name) and name != schema.get("title"):
            problems.append(
                f"{name}: title {schema.get('title')!r} does not match its key"
            )

    status = document.get("x-implementation-status", {})
    if status.get("milestone") == "M0" and document.get("paths"):
        # The document claims M0 (no paths implemented).  If paths appear, the
        # claim is stale and the document is overstating what exists.
        problems.append(
            "the document claims milestone M0 but declares paths; "
            "either the milestone or the status is wrong"
        )
    if status.get("milestone") == "M0" and not status.get("not_yet_implemented"):
        problems.append("an M0 document must state what is not yet implemented")

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the OpenAPI document")
    parser.add_argument("--path", type=Path, default=DEFAULT)
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"FAIL: {args.path} does not exist; run scripts/gen_openapi.py")
        return 1

    document = json.loads(args.path.read_text(encoding="utf-8"))
    problems = check(document)
    schemas = len(document.get("components", {}).get("schemas", {}))

    if problems:
        print(f"FAIL: {len(problems)} problem(s) in {args.path}")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"PASS: {args.path}")
    print(f"  schemas: {schemas}")
    print(f"  paths:   {len(document.get('paths', {}))}")
    print(f"  refs:    all resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
