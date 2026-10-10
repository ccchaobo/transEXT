"""Assembling the pieces a process needs: settings, database, workspace, queue.

Both processes that make up this service - the API and the worker - need the same four objects
built the same way, against the same data directory.  Building them twice, in two places, is how
the two would come to disagree about which database they are talking to, which is a class of bug
that shows up as "the job never runs" and costs an afternoon.

The objects are handed back in a dataclass rather than as four return values so a caller cannot
transpose them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rr_storage import Database, JobQueue, Workspace, create_database

from .settings import Settings

__all__ = ["Runtime", "open_runtime"]


@dataclass
class Runtime:
    settings: Settings
    database: Database
    workspace: Workspace
    queue: JobQueue

    @property
    def data_dir(self):
        return self.settings.data_dir


def open_runtime(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    workspace: Workspace | None = None,
    queue: JobQueue | None = None,
) -> Runtime:
    """Build a runtime, creating the data directory and schema if they are missing.

    Every argument is optional so a test can substitute one piece without rebuilding the rest.
    """
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if database is None:
        database = create_database(settings.data_dir)
        database.create_all()
    if workspace is None:
        workspace = Workspace(settings.workspace_root)
    if queue is None:
        queue = JobQueue(lease_seconds=settings.lease_seconds)
    return Runtime(settings=settings, database=database, workspace=workspace, queue=queue)
