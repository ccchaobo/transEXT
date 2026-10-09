# ADR 0002: Vendor PDF.js instead of installing it from npm

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Depends on: ADR 0001

## Context

The reader displays source pages. Two options exist for rendering PDF in a
browser: rely on the browser's built-in viewer (`<embed>`, `<iframe>`) or bundle
PDF.js.

The built-in viewer cannot be used for this product. The core feature is
bidirectional anchoring: clicking a block in the translation pane must scroll and
highlight the corresponding region in the source pane, and selecting a region in
the source must locate the block. A browser's native viewer renders into a plugin
or shadow surface with no API for "highlight this rectangle" or "give me the
pointer position in page coordinates". Text selection inside it cannot be
intercepted.

That leaves PDF.js, which exposes a canvas render at a chosen scale plus a
text layer with per-item transforms. That is exactly the information the
coordinate contract needs.

## Decision

Vendor a pinned PDF.js build under `apps/web/vendor/` and serve it as a static
file.

## Alternatives considered

**A. `<embed>` / native viewer.** No dependency at all, and the best possible
rendering fidelity. Rejected because it cannot support the product's central
interaction. This is not a close call: without an anchoring API there is no
feature.

**B. Load PDF.js from a CDN at runtime.** No vendored files. Rejected for three
reasons: the product is local-first and must work with no network; a CDN URL is
a remote code dependency in a tool that handles the user's unpublished
manuscripts; and a pinned CDN version still requires trusting the CDN's integrity
over time. `cdn.jsdelivr.net` and `unpkg.com` are also unreachable from this
environment, so it could not be verified here at all.

**C. Install from npm and copy the build output.** Reproducible version pinning
with a lockfile. Rejected as the *only* mechanism because `registry.npmjs.org`
is unreachable here while `registry.npmmirror.com` is reachable; a mirror-derived
artefact would then need committing anyway, and committing it while *also*
carrying an npm project would mean two sources of truth for the same file.

**D. Vendor a pinned build.** Chosen. The version is recorded in a README beside
the file, with its upstream source URL, the mirror actually used, and the file
hash, so the provenance is auditable and the update procedure is a documented
file replacement rather than a package-manager operation.

## Consequences

* The application works with no network access, which is a product requirement
  rather than a convenience.
* `apps/web/vendor/` contains third-party code. Its licence (Apache-2.0 for
  PDF.js) and version are recorded in the dependency inventory, and the licence
  text is kept alongside it.
* Updating PDF.js is manual. The tradeoff is accepted deliberately: the update
  procedure is written down, and a stale vendored dependency is visible in the
  inventory rather than being updated implicitly by a lockfile refresh.
* The build's file hash is recorded so a corrupted or substituted file is
  detectable.

## Revisit if

* The frontend gains a build step for another reason (ADR 0001), at which point
  a package manager becomes the natural mechanism.
* PDF.js's API changes in a way that breaks the anchoring integration, which
  makes the pinned version a liability that has to be updated deliberately.
