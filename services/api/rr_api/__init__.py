"""The HTTP layer: a local FastAPI app plus the read/export helpers behind it.

Kept separate from ``rr_services`` on purpose.  Services hold the rules (what is allowed, what
is recorded, what is refused); this package only translates them into requests and responses.
That is what lets the same rules be exercised from a script or a test without going through
HTTP, and it is why no policy lives in ``routes.py``.
"""

from __future__ import annotations

from .app import VERSION, WEB_ROOT, create_app, main

__all__ = ["VERSION", "WEB_ROOT", "create_app", "main"]
