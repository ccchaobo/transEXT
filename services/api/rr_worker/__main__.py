"""Run the worker: claim queued jobs and execute them, until stopped.

    python -m rr_worker                # poll forever, one job at a time
    python -m rr_worker --once         # run at most one job and exit
    python -m rr_worker --max-jobs 5   # run five jobs and exit

The worker is a separate process from the API on purpose.  A parse holds a lease, heartbeats and
can outlive an HTTP request by minutes; running it inside the web process would mean a page
refresh could not cancel it, a crash would take the API down with it, and "the job is still
running" would be reported by the process doing the running.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Sequence

from rr_adapters.translation import load_env_file
from rr_services import open_runtime

from . import Worker, recover

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rr_worker", description="Run the research-reader job worker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "process at most one job and exit, including when the queue is empty "
            "(useful from a script or a test)"
        ),
    )
    parser.add_argument(
        "--max-jobs", type=int, default=None, help="stop after this many jobs"
    )
    parser.add_argument(
        "--drain",
        action="store_true",
        help="stop once the queue is empty instead of polling for new work",
    )
    parser.add_argument(
        "--idle-grace",
        type=float,
        default=1.0,
        help="seconds the queue must stay empty before --drain/--once stops (default 1.0)",
    )
    parser.add_argument(
        "--poll", type=float, default=1.0, help="seconds to wait between polls (default 1.0)"
    )
    parser.add_argument(
        "--recover-after",
        type=float,
        default=3600.0,
        help=(
            "consider a job abandoned if it has not been touched for this many seconds and "
            "return it to the queue (default 3600)"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="do not print each job")
    args = parser.parse_args(argv)

    env_file = Path(os.environ.get("RR_ENV_FILE") or ".env")
    if env_file.is_file():
        load_env_file(env_file)

    runtime = open_runtime()

    # A job whose process died mid-run holds a lease that nobody is renewing.  Returning those to
    # the queue at start-up means a crash loses the work in flight rather than the whole queue.
    with runtime.database.session() as session:
        reclaimed = recover(session, age_seconds=args.recover_after)
    if reclaimed:
        print(f"reclaimed {reclaimed} abandoned job(s)", flush=True)

    stop = threading.Event()

    def _handle(signum, _frame):  # noqa: ANN001 - signal handler signature
        print(f"\nreceived signal {signum}; finishing the current job and stopping", flush=True)
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _handle)
            except (ValueError, OSError):
                # Not the main thread, or the platform does not allow it.  Ctrl-C still works.
                pass

    worker = Worker(
        runtime.settings,
        runtime.workspace,
        runtime.queue,
        database=runtime.database,
    )

    max_jobs = 1 if args.once else args.max_jobs
    if not args.quiet:
        print(
            f"worker started; data dir {runtime.settings.data_dir}, "
            f"lease {runtime.settings.lease_seconds}s, poll {args.poll}s",
            flush=True,
        )

    # ``stop`` takes a callable returning a bool, not the Event itself, so ``is_set`` is passed.
    # Per-job progress is deliberately not printed here: the job's own row and its append-only
    # event log already record every stage, attempt and error, and the reader displays those.
    # A second narration from this process could disagree with the record.
    #
    # ``--once`` and ``--drain`` exit when the queue empties.  Without that, a scripted one-shot
    # worker waits forever on an empty queue, because ``max_jobs`` counts jobs *done*.
    processed = worker.serve(
        poll_seconds=args.poll,
        stop=stop.is_set,
        max_jobs=max_jobs,
        exit_when_idle=args.once or args.drain,
        idle_grace=args.idle_grace,
    )
    if not args.quiet:
        print(f"worker stopped after {processed} job(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
