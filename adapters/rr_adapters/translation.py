"""Real translation providers.

The spec requires a replaceable, **real** translation service, and it forbids claiming
translation-quality results without one.  This module defines the interface and a single
implementation that speaks the OpenAI chat-completions protocol — which Ollama's cloud
API and most hosted services expose — so swapping the endpoint swaps the service without
touching the pipeline.

Two rules shape the design:

* **A failure is per unit, never per batch.**  A job that translates 200 paragraphs and
  fails on 7 must say so and keep the other 193, with the 7 named.  Every unit gets its
  own result record, so partial completion is expressible rather than inferred.
* **An unknown result is not a failure and must not be retried blindly.**  If a request
  times out, the service may or may not have processed it.  Retrying could double the
  charge; treating it as failed would be a lie.  It becomes
  :data:`EXTERNAL_UNKNOWN_RESULT` and the caller decides.

The API key is read from the environment and never returned in a response, logged, or
sent to the frontend.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

import httpx

from rr_contracts import ErrorCode

#: Bumped whenever the prompt below changes, because it is part of the cache key: a
#: translation produced under an older prompt must not be reused silently.
PROMPT_VERSION = "v1"

# Business error codes come from the shared contract instead of being invented here.
# The contract owns them because it also maps each one to an HTTP status (``HTTP_STATUS``)
# and marks which are retryable; a second vocabulary in this adapter would report the
# same failure two different ways and lose that mapping.
CODE_UNKNOWN_RESULT = ErrorCode.EXTERNAL_UNKNOWN_RESULT.value
CODE_UNAVAILABLE = ErrorCode.EXTERNAL_UNAVAILABLE.value
CODE_RATE_LIMITED = ErrorCode.EXTERNAL_RATE_LIMITED.value
CODE_AUTH_FAILED = ErrorCode.EXTERNAL_AUTH_FAILED.value
CODE_NO_BALANCE = ErrorCode.EXTERNAL_INSUFFICIENT_BALANCE.value
CODE_INVALID_REQUEST = ErrorCode.INVALID_REQUEST.value
CODE_TRANSLATION_FAILED = ErrorCode.TRANSLATION_FAILED.value

#: The module's name for "the service may or may not have processed this".
EXTERNAL_UNKNOWN_RESULT = CODE_UNKNOWN_RESULT


def code_for_status(status: int) -> str:
    """Map an HTTP status from the service onto the shared error vocabulary.

    Kept coarse on purpose: the status says *which class* of failure occurred, and the
    response body (truncated, in ``error_message``) says the rest.  Inventing a code per
    status would put values in the database that nothing else in the system knows.
    """
    if status in (401, 403):
        return CODE_AUTH_FAILED
    if status == 402:
        return CODE_NO_BALANCE
    if status == 429:
        return CODE_RATE_LIMITED
    if status >= 500:
        return CODE_UNAVAILABLE
    return CODE_INVALID_REQUEST


#: Language codes to the names used in the prompt.  Sending "zh" to a model usually
#: works, but "Chinese" removes any doubt.
LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "ru": "Russian",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get((code or "").strip().lower(), code or "the source language")


#: HTTP statuses worth another attempt.  408 and 429 are "too slow / too many", 5xx are
#: the service's own trouble.  A 4xx other than these means the request itself is wrong
#: and repeating it will not help.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

DEFAULT_SYSTEM_PROMPT = """\
You translate research papers from {source_language} into {target_language}.

Rules:
- Output only the translation. No preamble, no notes, no explanation, no quotes around it.
- Preserve mathematical notation, symbols and equations exactly as they appear.
- Preserve citation markers ([1], [2,3]), DOIs, URLs, e-mail addresses, figure and table
  numbers, variable names, and units exactly as they appear. Never translate or reorder them.
- Do not translate author names, affiliations, or the contents of a citation.
- Keep the paragraph as one paragraph. Do not add or remove sentences.
- If a term appears in the glossary below, translate it exactly as the glossary says.
{glossary_block}\
"""


# ---------------------------------------------------------------------------
# Wire types between the pipeline and a provider
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TranslationUnit:
    """One block's text, with the identity the result must come back under."""

    block_id: str
    text: str
    #: Free-form guidance for this unit: the section path, or the sentence before it.
    #: Providers may include it to improve pronoun and terminology resolution.
    context: str | None = None


@dataclass(slots=True)
class TranslationResult:
    """What happened to exactly one unit.

    ``status`` is deliberately three-valued.  ``failed`` means the service answered and
    the answer was unusable; ``unknown`` means we do not know whether it was processed.
    """

    block_id: str
    text: str = ""
    status: Literal["ok", "failed", "unknown"] = "ok"
    error_code: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    model: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.text.strip())


@dataclass(slots=True)
class TranslationOutcome:
    """A whole request's results, plus the accounting for it."""

    results: list[TranslationResult] = field(default_factory=list)
    provider: str = ""
    model: str | None = None
    prompt_version: str = PROMPT_VERSION
    usage: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def unknown_count(self) -> int:
        return sum(1 for r in self.results if r.status == "unknown")

    @property
    def is_complete(self) -> bool:
        """True only when every unit succeeded.  The caller decides what to do with it."""
        return bool(self.results) and self.ok_count == len(self.results)

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.finished_at or time.monotonic()) - self.started_at


class TranslationProvider(Protocol):
    """The replaceable translation service (spec: a real service, not a mock)."""

    name: str
    model: str | None

    def translate(
        self,
        units: Sequence[TranslationUnit],
        *,
        glossary: Sequence[tuple[str, str]] = (),
        source_language: str = "en",
        target_language: str = "zh",
        options: dict[str, Any] | None = None,
    ) -> TranslationOutcome:
        ...


class TranslationConfigError(RuntimeError):
    """Configuration is missing or unusable.  Raised before any network call."""


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OpenAITranslationConfig:
    """Everything needed to reach a chat-completions endpoint.

    ``base_url`` is the *service root* and must include the API version segment, e.g.
    ``https://ollama.com/v1``.  The path ``/chat/completions`` is appended.
    """

    base_url: str
    api_key: str
    model: str
    name: str = "openai-compatible"
    #: Seconds before a request is abandoned.  A timeout yields ``unknown``, not
    #: ``failed``, because the service may still be working.
    timeout_seconds: float = 120.0
    #: Simultaneous requests.  Hosted services usually rate-limit, so this is modest.
    max_concurrency: int = 4
    #: Attempts for *retryable* failures only (see RETRYABLE_STATUS).
    max_attempts: int = 3
    backoff_seconds: float = 1.5
    temperature: float = 0.2
    #: Passed through to the service when set.  Left unset by default: sending
    #: provider-specific fields to the wrong service is a common source of 400s.
    extra_body: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def cache_fingerprint(self) -> dict[str, Any]:
        """The parts of this configuration that change the output.

        Part of the cache key.  ``api_key``, timeouts and concurrency are excluded: they
        do not affect what the service returns.
        """
        return {
            "provider": self.name,
            "model": self.model,
            "temperature": self.temperature,
            "extra_body": self.extra_body,
            "prompt_version": PROMPT_VERSION,
        }


class OpenAIChatTranslationProvider:
    """Translates through any ``/chat/completions`` endpoint.

    One request per unit by default.  That is deliberate: batching several paragraphs
    into one request makes a failure ambiguous (which paragraph was dropped?) and lets a
    long input crowd out a short one.  Concurrency, not batching, is how this goes fast,
    and it keeps the per-unit failure contract exact.
    """

    def __init__(
        self,
        config: OpenAITranslationConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not config.base_url.strip():
            raise TranslationConfigError("base_url is empty")
        if not config.api_key.strip():
            raise TranslationConfigError("api_key is empty")
        if not config.model.strip():
            raise TranslationConfigError("model is empty")
        self.config = config
        self.name = config.name
        self.model = config.model
        self._client = client
        self._owns_client = client is None

    # -- lifecycle ---------------------------------------------------------

    def _build_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(
            timeout=httpx.Timeout(self.config.timeout_seconds, connect=15.0),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> OpenAIChatTranslationProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- prompt ------------------------------------------------------------

    def _system_prompt(
        self, glossary: Sequence[tuple[str, str]], source_language: str, target_language: str
    ) -> str:
        if glossary:
            lines = "\n".join(f"- {source} => {target}" for source, target in glossary)
            block = f"\nGlossary (source => {language_name(target_language)}):\n{lines}\n"
        else:
            block = ""
        # `replace`, not `str.format`.  A custom prompt that contains braces for any other
        # reason — a JSON example is the obvious one — would make `format` raise KeyError
        # before a single request is sent.  Only the three known placeholders are
        # substituted and everything else is left exactly as written.
        prompt = self.config.system_prompt
        prompt = prompt.replace("{source_language}", language_name(source_language))
        prompt = prompt.replace("{target_language}", language_name(target_language))
        prompt = prompt.replace("{glossary_block}", block)
        return prompt

    # -- one request -------------------------------------------------------

    def _payload(self, unit: TranslationUnit, system_prompt: str) -> dict[str, Any]:
        user_text = unit.text
        if unit.context:
            # Given as context only; the model is told not to translate it.
            user_text = (
                f"Context (do not translate, for reference only):\n{unit.context}\n\n"
                f"Translate this:\n{unit.text}"
            )
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": self.config.temperature,
            "stream": False,
        }
        body.update(self.config.extra_body)
        return body

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        message = choices[0].get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, str):
            return None
        return content.strip()

    def _translate_one(
        self, client: httpx.Client, unit: TranslationUnit, system_prompt: str
    ) -> TranslationResult:
        result = TranslationResult(block_id=unit.block_id, model=self.config.model)
        payload = self._payload(unit, system_prompt)
        last_error: str | None = None

        for attempt in range(1, self.config.max_attempts + 1):
            result.attempts = attempt
            try:
                response = client.post(self.config.endpoint(), json=payload)
            except httpx.TimeoutException as exc:
                # The service may have processed this.  Do not retry and do not call it
                # failed: record that the outcome is genuinely unknown.
                result.status = "unknown"
                result.error_code = EXTERNAL_UNKNOWN_RESULT
                result.error_message = f"timeout after {self.config.timeout_seconds}s: {exc}"
                return result
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.config.max_attempts:
                    time.sleep(self.config.backoff_seconds * (2 ** (attempt - 1)))
                    continue
                result.status = "failed"
                # The connection failed, so nothing was delivered: this is a plain
                # service-unavailable failure, not an unknown outcome.
                result.error_code = CODE_UNAVAILABLE
                result.error_message = last_error
                return result

            if response.status_code in RETRYABLE_STATUS and attempt < self.config.max_attempts:
                last_error = f"HTTP {response.status_code}"
                time.sleep(self.config.backoff_seconds * (2 ** (attempt - 1)))
                continue

            if response.status_code >= 400:
                result.status = "failed"
                result.error_code = code_for_status(response.status_code)
                # Truncated: an error body can be a whole HTML page.
                result.error_message = (response.text or "")[:400]
                return result

            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                result.status = "failed"
                result.error_code = CODE_TRANSLATION_FAILED
                result.error_message = f"response was not JSON: {exc}"
                return result

            text = self._extract_text(data)
            if text is None:
                result.status = "failed"
                result.error_code = CODE_TRANSLATION_FAILED
                result.error_message = (
                    "response contained no message content: "
                    f"{json.dumps(data)[:300]}"
                )
                return result
            if not text:
                result.status = "failed"
                result.error_code = CODE_TRANSLATION_FAILED
                result.error_message = "model returned an empty string"
                return result

            result.text = text
            result.status = "ok"
            usage = data.get("usage")
            if isinstance(usage, dict):
                result.usage = {k: v for k, v in usage.items() if isinstance(v, (int, float))}
            return result

        # Defensive: every branch above returns on the final attempt.
        result.status = "failed"  # pragma: no cover
        result.error_code = CODE_UNAVAILABLE  # pragma: no cover
        result.error_message = last_error or "no attempt succeeded"  # pragma: no cover
        return result  # pragma: no cover

    # -- the interface -----------------------------------------------------

    def translate(
        self,
        units: Sequence[TranslationUnit],
        *,
        glossary: Sequence[tuple[str, str]] = (),
        source_language: str = "en",
        target_language: str = "zh",
        options: dict[str, Any] | None = None,
    ) -> TranslationOutcome:
        outcome = TranslationOutcome(
            provider=self.name,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            started_at=time.monotonic(),
        )
        units = [u for u in units if u.text.strip()]
        if not units:
            outcome.finished_at = time.monotonic()
            return outcome

        options = options or {}
        system_prompt = self._system_prompt(glossary, source_language, target_language)
        workers = max(1, int(options.get("max_concurrency", self.config.max_concurrency)))
        client = self._build_client()

        try:
            if workers == 1 or len(units) == 1:
                results = [self._translate_one(client, u, system_prompt) for u in units]
            else:
                # Results are re-ordered to the input order afterwards, so the caller
                # never has to care about completion order.
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(
                        pool.map(lambda u: self._translate_one(client, u, system_prompt), units)
                    )
        finally:
            if self._owns_client:
                client.close()
                self._client = None

        order = {unit.block_id: i for i, unit in enumerate(units)}
        results.sort(key=lambda r: order.get(r.block_id, 0))
        outcome.results = results

        prompt_tokens = sum(int(r.usage.get("prompt_tokens") or 0) for r in results)
        completion_tokens = sum(int(r.usage.get("completion_tokens") or 0) for r in results)
        total_tokens = sum(int(r.usage.get("total_tokens") or 0) for r in results)
        outcome.usage = {
            "units": len(units),
            "ok": outcome.ok_count,
            "failed": outcome.failed_count,
            "unknown": outcome.unknown_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        outcome.finished_at = time.monotonic()
        return outcome


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Environment variables.  The API key is read here and nowhere else, so it cannot reach
#: a response body or the browser.
ENV_BASE_URL = "RR_TRANSLATION_BASE_URL"
ENV_API_KEY = "RR_TRANSLATION_API_KEY"
ENV_MODEL = "RR_TRANSLATION_MODEL"
ENV_NAME = "RR_TRANSLATION_NAME"
ENV_TIMEOUT = "RR_TRANSLATION_TIMEOUT"
ENV_CONCURRENCY = "RR_TRANSLATION_CONCURRENCY"
ENV_MAX_ATTEMPTS = "RR_TRANSLATION_MAX_ATTEMPTS"
ENV_TEMPERATURE = "RR_TRANSLATION_TEMPERATURE"

#: Default endpoint for Ollama's cloud service.
#:
#: **NOT VERIFIED IN THIS ENVIRONMENT.**  ``ollama.com`` resolves to a non-public address
#: here, so this value could not be tested and is very likely to need confirming against
#: your account's documentation.  Override it with ``RR_TRANSLATION_BASE_URL``; nothing
#: depends on the default being right, and a wrong one fails loudly with a clear HTTP
#: error rather than silently.
DEFAULT_OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"


def config_from_env(env: dict[str, str] | None = None) -> OpenAITranslationConfig | None:
    """Build a config from the environment, or ``None`` when no key is configured.

    Returning ``None`` rather than a placeholder provider is deliberate: a placeholder
    would let a caller believe translation is available when it is not, and the spec
    requires the real-call acceptance item to be reported as PENDING instead.
    """
    env = env if env is not None else dict(os.environ)
    api_key = (env.get(ENV_API_KEY) or "").strip()
    if not api_key:
        return None
    model = (env.get(ENV_MODEL) or "").strip()
    if not model:
        raise TranslationConfigError(
            f"{ENV_MODEL} is not set. Set it to the model id you want to use."
        )
    base_url = (env.get(ENV_BASE_URL) or "").strip() or DEFAULT_OLLAMA_CLOUD_BASE_URL

    def _float(name: str, default: float) -> float:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise TranslationConfigError(f"{name} is not a number: {raw!r}") from exc

    def _int(name: str, default: int) -> int:
        return int(_float(name, float(default)))

    return OpenAITranslationConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        name=(env.get(ENV_NAME) or "ollama-cloud").strip(),
        timeout_seconds=_float(ENV_TIMEOUT, 120.0),
        max_concurrency=max(1, _int(ENV_CONCURRENCY, 4)),
        max_attempts=max(1, _int(ENV_MAX_ATTEMPTS, 3)),
        temperature=_float(ENV_TEMPERATURE, 0.2),
    )


def provider_from_env(
    env: dict[str, str] | None = None,
) -> OpenAIChatTranslationProvider | None:
    """The provider to use, or ``None`` when translation is not configured."""
    config = config_from_env(env)
    if config is None:
        return None
    return OpenAIChatTranslationProvider(config)


def describe_configuration(env: dict[str, str] | None = None) -> dict[str, Any]:
    """What the service may report about translation configuration.

    Reports whether it is configured and which model/endpoint would be used.  The API key
    is reported only as a boolean, never echoed.
    """
    env = env if env is not None else dict(os.environ)
    api_key = (env.get(ENV_API_KEY) or "").strip()
    return {
        "configured": bool(api_key),
        "provider": (env.get(ENV_NAME) or "ollama-cloud").strip(),
        "model": (env.get(ENV_MODEL) or "").strip() or None,
        "base_url": (env.get(ENV_BASE_URL) or "").strip() or DEFAULT_OLLAMA_CLOUD_BASE_URL,
        "base_url_is_default": not (env.get(ENV_BASE_URL) or "").strip(),
        "prompt_version": PROMPT_VERSION,
        "max_concurrency": (env.get(ENV_CONCURRENCY) or "4").strip(),
    }


def load_env_file(path: str | Path) -> int:
    """Load ``KEY=VALUE`` lines from a file into ``os.environ``.

    A convenience so the key can live in a git-ignored file instead of a shell profile.
    Existing environment variables win, so a real environment always overrides the file.
    Returns how many variables were set.
    """
    path = Path(path)
    if not path.exists():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


__all__ = [
    "CODE_AUTH_FAILED",
    "CODE_INVALID_REQUEST",
    "CODE_NO_BALANCE",
    "CODE_RATE_LIMITED",
    "CODE_TRANSLATION_FAILED",
    "CODE_UNAVAILABLE",
    "CODE_UNKNOWN_RESULT",
    "DEFAULT_OLLAMA_CLOUD_BASE_URL",
    "DEFAULT_SYSTEM_PROMPT",
    "EXTERNAL_UNKNOWN_RESULT",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_CONCURRENCY",
    "ENV_MAX_ATTEMPTS",
    "ENV_MODEL",
    "ENV_NAME",
    "ENV_TEMPERATURE",
    "ENV_TIMEOUT",
    "LANGUAGE_NAMES",
    "OpenAIChatTranslationProvider",
    "OpenAITranslationConfig",
    "PROMPT_VERSION",
    "RETRYABLE_STATUS",
    "TranslationConfigError",
    "TranslationOutcome",
    "TranslationProvider",
    "TranslationResult",
    "TranslationUnit",
    "config_from_env",
    "code_for_status",
    "describe_configuration",
    "language_name",
    "load_env_file",
    "provider_from_env",
]
