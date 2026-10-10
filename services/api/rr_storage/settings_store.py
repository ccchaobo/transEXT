"""Read and write stored settings.

Deliberately thin.  The store holds strings and knows which of them are secret; it does not
know what any of them mean, and it does not validate them.  Interpretation belongs to the code
that uses the value, so a stored setting and an environment variable are read the same way.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Setting

__all__ = [
    "all_settings",
    "delete_setting",
    "get_setting",
    "secret_keys",
    "set_setting",
]


def get_setting(session: Session, key: str) -> str | None:
    """The stored value, or ``None`` when nothing is stored under that key."""
    row = session.get(Setting, key)
    return None if row is None else row.value


def set_setting(session: Session, key: str, value: str, *, is_secret: bool = False) -> None:
    """Store a value, replacing any previous one.

    An empty value is stored rather than deleted, because "explicitly set to nothing" and
    "never set" are different states: the first is a reader clearing a key they had typed, and
    the resolver has to be able to tell it apart from a key that was never there.
    """
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, is_secret=is_secret))
    else:
        row.value = value
        row.is_secret = is_secret
    session.flush()


def delete_setting(session: Session, key: str) -> bool:
    """Remove a key entirely.  Returns whether anything was removed."""
    result = session.execute(delete(Setting).where(Setting.key == key))
    session.flush()
    return bool(result.rowcount)


def all_settings(session: Session) -> dict[str, Any]:
    """Every stored setting, with secrets replaced by a boolean.

    The redaction happens here, at the only place that reads the whole table, rather than at
    each caller, so there is no path that returns a secret by forgetting to redact it.
    """
    rows = session.execute(select(Setting).order_by(Setting.key)).scalars().all()
    return {
        row.key: (bool(row.value) if row.is_secret else row.value)
        for row in rows
    }


def secret_keys(session: Session) -> set[str]:
    """Which stored keys are secret, so a caller can refuse to write them in the clear."""
    rows = session.execute(select(Setting.key).where(Setting.is_secret.is_(True))).scalars().all()
    return set(rows)
