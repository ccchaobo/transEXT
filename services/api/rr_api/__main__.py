"""``python -m rr_api`` - start the reader on loopback."""

from __future__ import annotations

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
