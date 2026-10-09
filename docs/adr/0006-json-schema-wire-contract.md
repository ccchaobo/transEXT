# ADR 0006: Pydantic models as the single wire contract

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Refines: spec §7, §9, §10

## Context

The frontend and the backend exchange documents, pages, blocks, translations,
jobs, annotations, terms, reading state and exports. Each of those has a shape,
and the same shape is needed in three places: to validate an incoming request, to
serialise a response, and to generate the API documentation.

The contract also carries decisions that are easy to lose if the shape is written
down more than once:

* page numbering is 1-based on the wire and 0-based internally (ADR 0008);
* a block's source location is a list of regions, not a single box, because a
  paragraph can continue across a page or a column;
* confidence is a level **and** a score, because a score without a level invites
  a consumer to invent its own threshold;
* every job carries a per-stage and per-unit status, because "partially
  succeeded" must be representable (spec §9);
* errors carry a machine-readable code, because the client's retry policy depends
  on it and must not be inferred from a message string.

## Decision

Define every request and response body **once**, as a Pydantic v2 model in
`packages/contracts/rr_contracts`, and derive the OpenAPI document from those
models. FastAPI's own request/response typing uses the same classes, so a model
cannot drift from the endpoint that serves it.

The package holds no behaviour: it is the vocabulary, not the logic. Anything that
needs to import the storage layer or an adapter is not allowed in it.

Two conventions are fixed here rather than in each caller:

* **Fields that must not be confused are named for their base.** A user-facing
  page number is `page_number`; there is no bare `page_index` on the wire at all.
  A caller cannot therefore pass the wrong one by accident — the wrong name does
  not exist.
* **Errors are enumerated.** `ErrorCode` lists the codes the client may branch on,
  and `RETRYABLE_CODES` states which are safe to retry. `HTTP_STATUS` maps each
  code to its status, so a handler does not choose a status independently of the
  code it returns.

## Alternatives considered

**A. Hand-written dictionaries and a separate OpenAPI file.** Maximum control.
Rejected: the schema, the validation and the documentation are three places to
update for one change, and nothing detects when they disagree. This is the failure
mode the decision exists to prevent.

**B. Dataclasses plus a serialiser.** Lighter than Pydantic. Rejected: validation
of untrusted input would have to be written by hand for every field, and that is
precisely where a parser boundary is most valuable (spec §11).

**C. A separate schema language (JSON Schema or Protobuf) as the source.** Good
for cross-language clients. Rejected for P0: there is one client, it is
JavaScript served by the same process, and a code-generation step would add a
build stage that ADR 0001 exists to avoid. Revisit if a second, non-JavaScript
client appears.

**D. Pydantic models as the single contract.** Chosen.

## Consequences

* The OpenAPI document at `/openapi.json` is an output of the contract, not a
  parallel description of it. It is therefore evidence, not documentation that can
  go stale.
* Validation happens at the boundary, so a malformed request is rejected before it
  reaches the database or an adapter.
* The frontend still cannot import the Python models. It consumes the OpenAPI
  document, and the client-side shapes are checked against it by tests rather than
  by sharing code.
* Adding a field is a change in one place, but it is also a change to the wire
  contract. Fields are added as optional or with a default unless the endpoint is
  genuinely new, so an older client keeps working.
* Enum values are part of the contract. Renaming one is a breaking change and must
  be treated as such.

## Verification

* The models are importable without importing storage or adapters, which is what
  keeps the package free of behaviour.
* `ErrorCode`, `RETRYABLE_CODES` and `HTTP_STATUS` are checked for mutual
  consistency: every code has a status, and every retryable code is a real code.
* The OpenAPI document is generated and stored as an M0 artifact, so the contract
  can be reviewed without starting the server.
