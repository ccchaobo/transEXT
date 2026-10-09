"""Application services: the layer that joins adapters, storage and the job queue.

Services own transactions and policy (what is allowed, what is recorded, what is refused);
adapters own nothing but the mechanics of talking to a library or a network service.
"""

from __future__ import annotations

from .documents import DocumentService, ImportResult
from .formats import FileRejected, FormatProbe, verify
from .parsing import PARSER_NAME, PARSER_VERSION, ParseOutcome, ParseService
from .settings import DEFAULT_DATA_DIR, Settings

__all__ = [
    "DEFAULT_DATA_DIR",
    "DocumentService",
    "FileRejected",
    "FormatProbe",
    "ImportResult",
    "PARSER_NAME",
    "PARSER_VERSION",
    "ParseOutcome",
    "ParseService",
    "Settings",
    "verify",
]
