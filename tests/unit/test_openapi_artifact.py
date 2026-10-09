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

    def test_provider_keys_are_not_part_of_the_client_contract(self, document: dict) -> None:
        # The frontend must never receive or send a provider key (spec 11).
        #
        # This checks *field names* rather than a substring search of the whole
        # document: the security scheme is legitimately an apiKey type (it is the
        # local session token), and the description legitimately says that provider
        # keys are never sent to the client.  A substring search flagged both of
        # those, which would have made the test fail for stating the rule.
        forbidden_names = {
            "provider_key", "provider_api_key", "openai_api_key",
            "api_key", "apikey", "access_token", "client_secret",
        }
        offenders: list[str] = []
        for schema_name, schema in document["components"]["schemas"].items():
            for field_name in (schema.get("properties") or {}):
                if field_name.lower() in forbidden_names:
                    offenders.append(f"{schema_name}.{field_name}")
            for required in schema.get("required", []):
                if required.lower() in forbidden_names:
                    offenders.append(f"{schema_name}.{required} (required)")
        assert offenders == [], (
            f"provider credentials must stay server-side: {offenders}"
        )

    def test_the_secure_scheme_is_documented(self, document: dict) -> None:
        schemes = document["components"]["securitySchemes"]
        assert schemes, "the loopback session token must be documented"
        assert any(
            entry["type"] == "apiKey" for entry in schemes.values()
        ), "a loopback guard is expected to be a token"


class TestErrorMappingReachesTheDocument:
    def test_declared_statuses_match_the_taxonomy(self) -> None:
        # The mapping lives in Python; this asserts it is non-trivial and stable
        # rather than restating each entry, which would just duplicate the table.
        assert set(HTTP_STATUS) == set(ErrorCode)
        assert ErrorResponse.model_fields["error"].annotation is not None
