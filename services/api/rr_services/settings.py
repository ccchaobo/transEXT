"""Runtime settings, read from the environment with defaults for a single local user.

Every limit here is a *guard*, not a performance target: it exists so one bad file cannot
exhaust the machine.  Defaults are chosen to be generous enough that a normal research
paper never meets them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_DATA_DIR = "RR_DATA_DIR"
ENV_MAX_FILE_MB = "RR_MAX_FILE_MB"
ENV_MAX_PAGES = "RR_MAX_PAGES"
ENV_LEASE_SECONDS = "RR_LEASE_SECONDS"
ENV_MAX_UNCOMPRESSED_MB = "RR_MAX_UNCOMPRESSED_MB"
ENV_MAX_ARCHIVE_RATIO = "RR_MAX_ARCHIVE_RATIO"
ENV_MAX_TRANSLATION_UNITS = "RR_MAX_TRANSLATION_UNITS"

#: Default data directory, relative to the process working directory.  Kept inside the
#: project so it is obvious where the originals live and easy to delete; override with
#: RR_DATA_DIR to put it elsewhere.
DEFAULT_DATA_DIR = "var"


@dataclass(frozen=True)
class Settings:
    """Everything the services need to know about limits and locations."""

    data_dir: Path
    #: Largest upload accepted, in bytes.  Enforced while streaming, so an oversized
    #: upload cannot fill the disk before it is refused.
    max_file_bytes: int = 200 * 1024 * 1024
    #: Largest page count accepted.  A PDF is a program; a 50 000 page one is a denial of
    #: service against your own machine.
    max_pages: int = 2_000
    #: Largest total expansion of a DOCX (a ZIP), and its maximum ratio.
    max_uncompressed_bytes: int = 400 * 1024 * 1024
    max_archive_ratio: float = 200.0
    #: How long a worker's claim on a job stays valid without a heartbeat.  A worker that
    #: dies is recovered after this.
    lease_seconds: int = 120
    #: Refuse a translation job larger than this many blocks before spending anything.
    #: The spec requires asking before external cost, and this is the last line of defence
    #: if a caller forgets to.
    max_translation_units: int = 5_000

    grobid_url: str = ""
    grobid_timeout_seconds: int = 45

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'reader.sqlite3').as_posix()}"

    @property
    def workspace_root(self) -> Path:
        return self.data_dir

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = env if env is not None else dict(os.environ)

        def _int(name: str, default: int) -> int:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a whole number, got {raw!r}") from exc
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
            return value

        def _float(name: str, default: float) -> float:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a number, got {raw!r}") from exc
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
            return value

        data_dir = Path((env.get(ENV_DATA_DIR) or "").strip() or DEFAULT_DATA_DIR).expanduser()
        return cls(
            data_dir=data_dir.resolve(),
            max_file_bytes=_int(ENV_MAX_FILE_MB, 200) * 1024 * 1024,
            max_pages=_int(ENV_MAX_PAGES, 2_000),
            max_uncompressed_bytes=_int(ENV_MAX_UNCOMPRESSED_MB, 400) * 1024 * 1024,
            max_archive_ratio=_float(ENV_MAX_ARCHIVE_RATIO, 200.0),
            lease_seconds=_int(ENV_LEASE_SECONDS, 120),
            max_translation_units=_int(ENV_MAX_TRANSLATION_UNITS, 5_000),
            grobid_url=(env.get("RR_GROBID_URL") or "").strip(),
            grobid_timeout_seconds=min(60, _int("RR_GROBID_TIMEOUT",45)),
        )


__all__ = [
    "DEFAULT_DATA_DIR",
    "ENV_DATA_DIR",
    "ENV_LEASE_SECONDS",
    "ENV_MAX_ARCHIVE_RATIO",
    "ENV_MAX_FILE_MB",
    "ENV_MAX_PAGES",
    "ENV_MAX_TRANSLATION_UNITS",
    "ENV_MAX_UNCOMPRESSED_MB",
    "Settings",
]
