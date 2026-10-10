"""Tests for the generated OpenAPI document.

The document is an output of the wire contract, so these tests check the thing
that makes it trustworthy: that it resolves internally, that every model is
represented, and that it does not claim more than is implemented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rr_contracts import ErrorCode, ErrorResponse, HTTP_STATUS

REPO = Path(__file__).resolve().parent.parent.parent
DOCUMENT_PATH = REPO / "packages" / "contracts" / "openapi.json"


@pytest.fixture(scope="module")
def document() -> dict:
    """The committed document, which must have been generated."""
    if not DOCUMENT_PATH.exists():
        pytest.skip(
            "packages/contracts/openapi.json has not been generated; "
            "run scripts/gen_openapi.py"
        )
    return json.loads(DOCUMENT_PATH.read_text(encoding="utf-8"))


class TestDocumentShape:
    def test_it_declares_a_supported_openapi_version(self, document: dict) -> None:
        assert document["openapi"].startswith("3.")

    def test_it_has_an_identifier(self, document: dict) -> None:
        assert document["info"]["title"]
        assert document["info"]["version"]

    def test_it_binds_to_loopback_by_default(self, document: dict) -> None:
        # Binding a public interface by default would expose a local document
        # store to the network, so the default server is loopback.
        urls = [server["url"] for server in document["servers"]]
        assert all(
            url.startswith(("http://127.0.0.1", "http://localhost")) for url in urls
        ), urls


class TestReferences:
    def test_every_reference_resolves(self, document: dict) -> None:
        # A dangling reference breaks client generation with a confusing error.
        from scripts.check_openapi import check

        problems = [p for p in check(document) if "dangling" in p]
        assert problems == [], problems

    def test_the_document_passes_all_consistency_checks(self, document: dict) -> None:
        from scripts.check_openapi import check

        assert check(document) == []


class TestSchemas:
    def test_every_wire_model_is_present(self, document: dict) -> None:
        import pydantic

        import rr_contracts

        schemas = document["components"]["schemas"]
        for name in dir(rr_contracts):
            value = getattr(rr_contracts, name)
            if (
                isinstance(value, type)
                and issubclass(value, pydantic.BaseModel)
                and value is not pydantic.BaseModel
            ):
                assert name in schemas, f"{name} is missing from the document"

    def test_the_error_taxonomy_is_represented(self, document: dict) -> None:
        # A client needs the codes to branch on them; a bare "string" field would
        # leave the client guessing at every value.
        schema = document["components"]["schemas"]["ErrorBody"]
        code = schema["properties"]["code"]
        # Pydantic renders a named enum as a ref to its own schema, and an inline
        # Literal as oneOf of consts; both shapes are accepted so this test does
        # not break when a field's declaration style changes.
        if "$ref" in code:
            name = code["$ref"].rsplit("/", 1)[-1]
            enum_schema = document["components"]["schemas"][name]
            values = set(enum_schema.get("enum", []))
            for entry in enum_schema.get("oneOf", []):
                if entry.get("const") is not None:
                    values.add(entry["const"])
        else:
            values = set(code.get("enum", []))
            for entry in code.get("oneOf", []):
                if entry.get("const") is not None:
                    values.add(entry["const"])
        declared = {item.value for item in ErrorCode}
        assert values, "no error codes reached the document"
        assert values <= declared, f"undeclared codes in the document: {values - declared}"

    def test_the_page_number_carries_its_documented_meaning(self, document: dict) -> None:
        # The 1-based/0-based distinction has to reach whoever reads the document,
        # not only whoever reads the Python.
        page = document["components"]["schemas"]["PageWire"]["properties"]
        assert "1-based" in page["page_number"]["description"].lower()
        assert "0-based" in page["index"]["description"].lower()

    def test_rotation_is_restricted_to_quarter_turns(self, document: dict) -> None:
        page = document["components"]["schemas"]["PageWire"]["properties"]
        rotation = page["rotation"]
        if "$ref" in rotation:
            name = rotation["$ref"].rsplit("/", 1)[-1]
            values = [
                entry.get("const")
                for entry in document["components"]["schemas"][name].get("oneOf", [])
            ]
        else:
            values = rotation.get("enum", [])
        assert sorted(v for v in values if v is not None) == [0, 90, 180, 270]


class TestImplementationHonesty:
    def test_the_document_does_not_claim_unimplemented_paths(self, document: dict) -> None:
        # At M0 no endpoint exists.  Declaring paths would describe an API that
        # cannot be called, which is exactly the kind of claim this project must
        # not make.
        status = document.get("x-implementation-status", {})
        if status.get("milestone") == "M0":
            assert document["paths"] == {}, document["paths"]

    def test_the_status_says_what_is_not_implemented(self, document: dict) -> None:
        status = document.get("x-implementation-status", {})
        assert status.get("not_yet_implemented"), (
            "the document must state what is missing, or it reads as complete"
        )

    def test_the_error_response_is_reusable(self, document: dict) -> None:
        responses = document["components"]["responses"]
        assert "BusinessError" in responses
        schema = responses["BusinessError"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("ErrorResponse")

    def test_provider_keys_are_never_returned_by_any_response(self, document: dict) -> None:
        """A credential may be *sent* to this service.  It may never be sent back.

        **This test replaced a stricter one, deliberately, at the owner's direction.**  The
        previous version asserted that no field named ``api_key`` appeared anywhere in
        ``components.schemas`` - which enforced the spec 11 rule that the frontend neither
        receives nor sends a provider key.  The reader asked to be able to enter a base URL, a
        model id and an API key in the settings panel, and a key typed into a panel is a key the
        browser sends.  The two cannot both hold, and the owner chose the panel.

        So the rule is now the one that actually protects something, and it is checked more
        precisely than before:

        * a secret-named field is allowed **only** in a schema reachable from a ``requestBody``;
        * it must not appear in any schema a client can *receive* - not in a success response,
          not in the error envelope, not in any other declared response;
        * and it may not be required, because requiring a key would make "leave it unchanged"
          impossible to express.

        A blanket name search could not tell those cases apart.  This can, and it fails if the
        redaction in the settings layer ever lets a value into a response model.
        """
        forbidden_names = {
            "provider_key", "provider_api_key", "openai_api_key",
            "api_key", "apikey", "access_token", "client_secret",
        }
        schemas = document["components"]["schemas"]

        def ref_name(node: dict) -> str | None:
            ref = node.get("$ref")
            return ref.rsplit("/", 1)[-1] if ref else None

        def names_in_schema(name: str) -> list[str]:
            schema = schemas.get(name) or {}
            found = [f for f in (schema.get("properties") or {}) if f.lower() in forbidden_names]
            found += [
                f"{r} (required)"
                for r in schema.get("required", [])
                if r.lower() in forbidden_names
            ]
            return found

        # Every schema a client can receive.
        received: dict[str, set[str]] = {}
        for path, operations in document["paths"].items():
            for method, operation in operations.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                for status, response in (operation.get("responses") or {}).items():
                    for media in (response.get("content") or {}).values():
                        name = ref_name(media.get("schema") or {})
                        if name:
                            received.setdefault(name, set()).add(f"{method.upper()} {path} {status}")

        leaks: list[str] = []
        for name, where in received.items():
            for field_name in names_in_schema(name):
                leaks.append(f"{name}.{field_name} reachable from {sorted(where)}")
        # Components registered as reusable *responses* count too.
        for name, response in (document["components"].get("responses") or {}).items():
            for media in (response.get("content") or {}).values():
                schema_name = ref_name(media.get("schema") or {})
                for field_name in (names_in_schema(schema_name) if schema_name else []):
                    leaks.append(f"{schema_name}.{field_name} in components.responses/{name}")
        assert leaks == [], f"a provider credential can be read back: {leaks}"

        # And the other half of the rule, so this is not satisfied by declaring nothing: the
        # write path must exist and must not require the key.
        write_schemas: set[str] = set()
        for operations in document["paths"].values():
            for method, operation in operations.items():
                if method not in {"post", "put", "patch"}:
                    continue
                for media in ((operation.get("requestBody") or {}).get("content") or {}).values():
                    name = ref_name(media.get("schema") or {})
                    if name:
                        write_schemas.add(name)
        assert write_schemas, "no request body schemas found; the scan above proved nothing"
        declared = [
            name for name in write_schemas if any(
                f.lower() in forbidden_names for f in (schemas.get(name, {}).get("properties") or {})
            )
        ]
        assert declared, (
            "no write-only credential field is declared; if key entry was removed, this test "
            "and the settings panel must be removed together rather than left asserting nothing"
        )
        for name in declared:
            assert "api_key" not in {
                r for r in schemas[name].get("required", [])
            }, f"{name} requires an api_key, so a reader could not edit other fields without resending it"

    def test_no_security_scheme_claims_auth_that_is_not_implemented(self, document: dict) -> None:
        """An earlier revision of this test asserted the opposite, and it was wrong.

        It required ``components.securitySchemes`` to declare an ``apiKey`` header named
        ``X-Session-Token``.  The generated document did declare exactly that, so the test passed
        - but the implementation has never read that header.  A client following this document
        would have sent a token that does nothing, and the document would have implied the
        service was authenticated when it is not.  A test that pins a claim the code does not
        honour is worse than no test, because it makes the claim look verified.

        What actually guards the service is a same-origin check on state-changing requests, which
        is a browser-origin check and is described in the document's own description.  So the
        assertion is now that no security scheme exists, and that the description says what does.
        """
        components = document["components"]
        assert not components.get("securitySchemes"), (
            "the service has no authentication; declaring a security scheme would tell clients "
            "to send a credential that is never read"
        )
        description = document["info"]["description"].lower()
        assert "no authentication" in description
        assert "origin" in description or "referer" in description


class TestErrorMappingReachesTheDocument:
    def test_declared_statuses_match_the_taxonomy(self) -> None:
        # The mapping lives in Python; this asserts it is non-trivial and stable
        # rather than restating each entry, which would just duplicate the table.
        assert set(HTTP_STATUS) == set(ErrorCode)
        assert ErrorResponse.model_fields["error"].annotation is not None
