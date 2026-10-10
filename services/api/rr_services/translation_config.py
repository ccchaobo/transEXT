"""Where the translation configuration actually comes from.

A value can arrive from three places, and the reader has to be able to say which one won:

1. **the environment** (or ``.env``), which is a deliberate override - it is how a script, a
   shortcut or a one-off run pins a model without touching stored state;
2. **the settings store**, which is what the reader's own settings panel writes, and which the
   worker sees without being restarted;
3. **the built-in default**, which today is only the Ollama cloud endpoint.

Environment wins over stored settings.  That ordering is chosen for one reason: an environment
variable set for a single command must mean what it says, and a stored value silently outranking
it would be a mystery.  The cost of the choice is that a stale variable makes the settings panel
appear to do nothing, so every response names the fields the environment is currently overriding
(``env_overrides``) rather than leaving the reader to work it out.

This module is the single place that knows the precedence.  Both the API and the worker resolve
through it, so they cannot disagree about which endpoint is configured - which matters, because
they are separate processes and a disagreement would show up as a job that the API queued and
the worker then refused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rr_adapters.translation import (
    DEFAULT_OLLAMA_CLOUD_BASE_URL,
    PROMPT_VERSION,
    SECRET_FIELDS,
    SETTING_FIELDS,
    OpenAIChatTranslationProvider,
    OpenAITranslationConfig,
    TranslationConfigError,
    config_from_values,
    values_from_env,
)
from rr_storage import all_settings, get_setting, set_setting

__all__ = [
    "SETTING_PREFIX",
    "ResolvedTranslationConfig",
    "clear_translation_settings",
    "describe_translation_configuration",
    "provider_from_resolved",
    "read_translation_settings",
    "resolve_translation_config",
    "write_translation_settings",
]

#: Stored keys are namespaced so the one settings table can hold other things later.
SETTING_PREFIX = "translation."

#: What each field becomes when nothing is set anywhere.  Empty means "no default".
DEFAULTS: dict[str, str] = {
    "base_url": DEFAULT_OLLAMA_CLOUD_BASE_URL,
    "model": "",
    "name": "ollama-cloud",
    "timeout_seconds": "120",
    "max_concurrency": "4",
    "max_attempts": "3",
    "temperature": "0.2",
    "api_key": "",
}


def _setting_key(field_name: str) -> str:
    return f"{SETTING_PREFIX}{field_name}"


@dataclass(frozen=True)
class ResolvedTranslationConfig:
    """The final values, where each came from, and whether configuration is usable."""

    values: dict[str, str]
    sources: dict[str, str]
    env_overrides: list[str] = field(default_factory=list)
    stored: dict[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        """A key and a model are both present.  Either missing means nothing can be sent."""
        return bool(self.values.get("api_key")) and bool(self.values.get("model"))

    @property
    def has_api_key(self) -> bool:
        return bool(self.values.get("api_key"))

    @property
    def base_url_is_default(self) -> bool:
        return self.sources.get("base_url") == "default"

    def to_config(self) -> OpenAITranslationConfig | None:
        """The adapter config, or ``None`` when no key is set anywhere."""
        return config_from_values(self.values)

    def describe(self) -> dict[str, Any]:
        """The redacted view.  The key is reported as a boolean and never echoed."""
        return {
            "configured": self.configured,
            "has_api_key": self.has_api_key,
            "provider": self.values.get("name") or "ollama-cloud",
            "model": self.values.get("model") or None,
            "base_url": self.values.get("base_url") or DEFAULT_OLLAMA_CLOUD_BASE_URL,
            "base_url_is_default": self.base_url_is_default,
            "timeout_seconds": _as_float(self.values.get("timeout_seconds"), 120.0),
            "max_concurrency": _as_int(self.values.get("max_concurrency"), 4),
            "max_attempts": _as_int(self.values.get("max_attempts"), 3),
            "temperature": _as_float(self.values.get("temperature"), 0.2),
            "prompt_version": PROMPT_VERSION,
            "sources": dict(self.sources),
            # Named explicitly, because this is the one thing that makes the settings panel
            # look broken when it is working exactly as designed.
            "env_overrides": list(self.env_overrides),
            "stored_keys": sorted(self.stored),
        }


def _as_float(raw: str | None, default: float) -> float:
    try:
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _as_int(raw: str | None, default: int) -> int:
    return int(_as_float(raw, float(default)))


def resolve_translation_config(
    session: Any | None = None, env: dict[str, str] | None = None
) -> ResolvedTranslationConfig:
    """Merge environment, stored settings and defaults, recording where each value came from.

    ``session`` may be ``None``, in which case only the environment and the defaults are
    consulted.  That is what the request handler does for a read-only view when opening a
    session would be wasteful, and it is never what the worker does.
    """
    from_env = values_from_env(env)
    from_store: dict[str, str] = {}
    stored_view: dict[str, Any] = {}
    secret_stored: set[str] = set()
    if session is not None:
        stored_view = all_settings(session)
        for field_name in SETTING_FIELDS:
            raw = get_setting(session, _setting_key(field_name))
            if raw is not None:
                from_store[field_name] = raw
                if field_name in SECRET_FIELDS:
                    secret_stored.add(field_name)

    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    overridden: list[str] = []
    for field_name in SETTING_FIELDS:
        if from_env.get(field_name):
            values[field_name] = from_env[field_name]
            sources[field_name] = "env"
            if from_store.get(field_name):
                # Both are set and the environment is winning.  Say so.
                overridden.append(field_name)
        elif from_store.get(field_name):
            values[field_name] = from_store[field_name]
            sources[field_name] = "settings"
        else:
            values[field_name] = DEFAULTS.get(field_name, "")
            sources[field_name] = "default"
    return ResolvedTranslationConfig(
        values=values,
        sources=sources,
        env_overrides=sorted(overridden),
        stored=stored_view,
    )


def describe_translation_configuration(
    session: Any | None = None, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """The redacted description, for ``/health`` and the configuration endpoint."""
    return resolve_translation_config(session, env).describe()


def provider_from_resolved(
    resolved: ResolvedTranslationConfig,
) -> OpenAIChatTranslationProvider | None:
    """Build a provider from resolved values, or ``None`` when translation is unavailable."""
    config = resolved.to_config()
    if config is None:
        return None
    return OpenAIChatTranslationProvider(config)


def read_translation_settings(session: Any) -> dict[str, Any]:
    """Only the stored translation settings, for the settings endpoint's own view."""
    stored: dict[str, Any] = {}
    for field_name in SETTING_FIELDS:
        raw = get_setting(session, _setting_key(field_name))
        if raw is None:
            continue
        stored[field_name] = bool(raw) if field_name in SECRET_FIELDS else raw
    return stored


def write_translation_settings(session: Any, changes: dict[str, str | None]) -> dict[str, Any]:
    """Apply a partial update and return the stored view afterwards.

    ``None`` means "leave alone" and ``""`` means "clear", which is why the two cannot be
    collapsed into one sentinel: a reader editing only the model must not silently wipe the
    key they already entered, and a reader clearing the key must actually clear it.

    Unknown field names raise, rather than being stored and ignored - a typo that appears to
    save is worse than an error.
    """
    unknown = sorted(set(changes) - set(SETTING_FIELDS))
    if unknown:
        raise TranslationConfigError(
            f"unknown translation setting(s): {', '.join(unknown)}.  "
            f"Known fields: {', '.join(sorted(SETTING_FIELDS))}"
        )
    for field_name, value in changes.items():
        if value is None:
            continue
        stripped = str(value).strip()
        set_setting(
            session,
            _setting_key(field_name),
            stripped,
            is_secret=field_name in SECRET_FIELDS,
        )
    session.commit()
    return read_translation_settings(session)


def clear_translation_settings(session: Any) -> int:
    """Remove every stored translation setting, so the environment and defaults decide again."""
    from rr_storage import delete_setting

    removed = 0
    for field_name in SETTING_FIELDS:
        if delete_setting(session, _setting_key(field_name)):
            removed += 1
    session.commit()
    return removed
