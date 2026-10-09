# ADR 0007: One worker process with leased, fenced jobs

* Status: accepted
* Date: 2026-10-09
* Deciders: implementation
* Refines: spec §9

## Context

Translation and OCR are slow, can fail partway through, and produce results that
cost money. The spec requires that partial completion is visible, that retrying a
failed item does not redo work that succeeded, and that a job whose worker died
does not stay "running" forever.

The deployment target is a single user, single machine, local backend. There is
no cluster and no external queue broker.

## Decision

Store jobs in the same SQLite database as everything else, and run exactly one
worker process that claims jobs transactionally.

The mechanism, in the order it matters:

* **Transactional claim.** A worker claims a job with a conditional `UPDATE ...
  WHERE status = 'queued'` and checks the affected row count. Two workers racing
  cannot both win, because SQLite serialises the write and the loser's `WHERE`
  no longer matches. A read-then-write claim would have a window where both
  workers believe they own the job.
* **Lease and heartbeat.** A claim sets a lease expiry. The worker extends it as
  it works. A job whose lease has expired is reclaimable, which is how a crashed
  worker's job becomes runnable again instead of being stuck.
* **Attempt fencing.** Each attempt has an identity. Writes from an attempt that
  has been superseded are rejected rather than applied. Without this, a worker
  that was declared dead, whose job was reclaimed and finished by a second
  worker, can come back and overwrite the finished result with a stale one.
* **Idempotency keys.** Enqueueing the same logical work twice returns the
  existing job instead of creating a second one, so a double-click or a retried
  request does not pay twice.
* **Per-unit status.** A job records completion per unit, which is what makes
  `partially_succeeded` a real state rather than a summary label.

## Alternatives considered

**A. A separate queue service (Redis, RabbitMQ).** Battle-tested semantics and
horizontal scale. Rejected: it adds an installation step and a second state store
to a single-user local tool, and it moves job state away from the transaction
that changes document state. The consistency this buys is not needed at one
worker; the operational cost is immediate.

**B. In-process background tasks (`BackgroundTasks`, threads).** No persistence,
no schema, very little code. Rejected: job state would be lost on restart, which
directly violates the requirement that partial completion and failure are visible
and resumable. A user who closes the app mid-translation would lose the record of
what was done.

**C. Multiple workers from the start.** More throughput. Rejected as premature:
it multiplies the concurrency surface (claim races, lease timing, SQLite write
contention) for a single-user tool whose bottleneck is a remote API call, not
local CPU. The claim and fencing design does not prevent adding workers later.

**D. One worker, leased and fenced jobs in SQLite.** Chosen.

## Consequences

* A crash cannot leave a job stuck in `running`: `reconcile_stale_jobs` reclaims
  expired leases at startup and the worker re-claims them.
* SQLite must run in WAL mode with a busy timeout, because the API process and
  the worker process both write. This is configured explicitly rather than left
  at defaults; the default rollback journal would produce avoidable `SQLITE_BUSY`
  errors.
* The fence is the part most likely to be dropped by a later refactor, because
  its benefit only appears in a rare interleaving. It is therefore covered by a
  test that simulates the interleaving explicitly (a stale attempt writing after
  its job was reclaimed and finished) rather than relying on the happy path.
* Throughput is one job at a time. For a single user translating one paper this
  is not a constraint; the work is dominated by waiting on the provider.
* Retry semantics are per unit, so retrying after a partial failure re-requests
  only what failed. This is a correctness property that also avoids paying twice
  for successful units.

## Revisit if

* Concurrent users or a shared server become a requirement, which makes a real
  broker worth its cost.
* The provider's rate limits make parallel unit-level requests useful, at which
  point in-job concurrency (not multi-worker) is the first step.
