"""Shared test configuration.

The default pytest temporary root lives under the user profile, which is not
writable in this development environment, so any test that needs a scratch file
fails during fixture setup with a permission error rather than a real assertion.
Pointing the temporary root inside the repository keeps the suite runnable
wherever it is checked out, and keeps scratch PDFs on the same volume as the
project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent
_TMP_ROOT = _REPO_ROOT / ".pytest_tmp"


def pytest_configure(config: pytest.Config) -> None:
    """Redirect the temporary root into the repository before fixtures run."""
    if config.option.basetemp:
        return
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(_TMP_ROOT)
