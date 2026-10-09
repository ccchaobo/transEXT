# ADR 0001: Serve the web frontend without an npm build step

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation

## Context

The frontend is a local, single-user reading interface. It needs a PDF renderer,
a two-pane layout, and bidirectional highlighting. The conventional choice is a
framework (React/Vue/Svelte) with a bundler, which brings a toolchain: a package
manager, a lockfile, a dev server, and a production build whose output has to be
regenerated whenever the backend serves it.

This environment cannot reach `registry.npmjs.org`. Measured:

| Host | Result |
| --- | --- |
| `registry.npmjs.org` | connection closed during TLS handshake |
| `cdn.jsdelivr.net` | same failure |
| `unpkg.com` | same failure |
| `registry.npmmirror.com` | reachable |
| `pypi.tuna.tsinghua.edu.cn` | reachable |

A mirror is reachable, so npm is not impossible in principle. The question is
whether a build step earns its cost.

## Decision

Serve the frontend as plain ES modules, HTML and CSS directly from the FastAPI
application. No bundler, no transpiler, no package manager on the critical path.
Vendored third-party browser code (PDF.js) is committed under `apps/web/vendor/`
and served as static files.

## Alternatives considered

**A. React/Vue with Vite.** Best ecosystem and component model. Cost: a Node
toolchain, a build artefact that must be rebuilt and re-verified on every change,
a lockfile that cannot be updated from the primary registry, and a second
dependency supply chain to audit for a local single-user tool. The reader UI is
one screen with a list, two panes and a toolbar; the component-model benefit is
small relative to the toolchain cost.

**B. Server-rendered HTML with progressive enhancement.** Simplest possible
deployment, no client dependency at all. Rejected because bidirectional
highlighting between two independently scrolling panes needs synchronous
scroll/selection handling and per-block DOM updates; doing that over the network
per interaction adds latency to exactly the interaction the product is about.

**C. Plain ES modules, no build.** Chosen. What is given up: TypeScript type
checking in the frontend, JSX ergonomics, and tree-shaking for hand-written code.
What is gained: the served files are the authored files, so a change is
verifiable by reading the file that the browser received; there is no build step
that can silently differ from the source; the dependency surface is exactly the
vendored PDF.js.

The loss of TypeScript is mitigated where it matters: the wire contract is
defined once in Pydantic models and is checked on the server, and the frontend
carries a small runtime contract check for the shapes it depends on. That is a
weaker guarantee than a type checker, and it is stated as a limitation rather
than presented as equivalent.

## Consequences

* Backend and frontend changes ship as the same artefact; the app is started with
  one command and there is no separate dev server.
* The frontend has no dependency manifest. Updating PDF.js is a manual,
  reviewable file replacement (see ADR 0002).
* Frontend code must be written for browsers that support ES modules with no
  transpilation. For a local tool on a current browser this is not a constraint.
* There is no frontend test runner in this environment and no browser
  automation. Frontend verification is therefore DOM-contract unit tests plus
  documented manual steps. This is recorded in the limitations document; it is
  not a claim of equivalent coverage.

## Revisit if

* The UI grows beyond roughly one screen's worth of components, at which point
  hand-written DOM updates become the dominant source of bugs.
* The frontend needs to be distributed independently of the backend.
* Browser automation becomes available and end-to-end UI tests are required by
  acceptance, at which point a build step would buy testability.
