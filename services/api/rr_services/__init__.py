"""Application services: the layer that joins adapters, storage and the job queue.

Services own transactions and policy (what is allowed, what is recorded, what is refused);
adapters own nothing but the mechanics of talking to a library or a network service.
"""

from __future__ import annotations

from .documents import DocumentService, ImportResult
from .formats import FileRejected, FormatProbe, verify
from .parsing import PARSER_NAME, PARSER_VERSION, ParseOutcome, ParseService
from .runtime import Runtime, open_runtime
from .settings import DEFAULT_DATA_DIR, Settings
from .translation import (
    GLOSSARY_VERSION_NONE,
    PlannedUnit,
    TranslationBudgetExceeded,
    TranslationNotConfigured,
    TranslationPlan,
    TranslationRun,
    TranslationService,
    cache_key,
    error_code_for,
)

__all__ = [
    "DEFAULT_DATA_DIR",
    "DocumentService",
    "FileRejected",
    "FormatProbe",
    "GLOSSARY_VERSION_NONE",
    "ImportResult",
    "PARSER_NAME",
    "PARSER_VERSION",
    "ParseOutcome",
    "ParseService",
    "PlannedUnit",
    "Runtime",
    "Settings",
    "TranslationBudgetExceeded",
    "TranslationNotConfigured",
    "TranslationPlan",
    "TranslationRun",
    "TranslationService",
    "cache_key",
    "error_code_for",
    "open_runtime",
    "verify",
]
