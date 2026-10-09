"""Tests for the translation provider.

This module talks to a paid, external service, so its failure handling is the part worth
testing hardest.  Three claims are load-bearing and each is checked here:

* a failure is recorded per unit, so a batch can be partially successful;
* a timeout is ``unknown``, not ``failed``, because the service may have processed it and
  a blind retry could be charged twice;
* the API key never appears in anything the service reports.

Everything runs against ``httpx.MockTransport``, so no test touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from rr_contracts import ErrorCode
from rr_adapters.translation import (
    EXTERNAL_UNKNOWN_RESULT,
    PROMPT_VERSION,
    OpenAIChatTranslationProvider,
    OpenAITranslationConfig,
    TranslationConfigError,
    TranslationUnit,
    code_for_status,
    config_from_env,
    describe_configuration,
    language_name,
    load_env_file,
    provider_from_env,
)

ENV = {
    "RR_TRANSLATION_API_KEY": "test-key",
    "RR_TRANSLATION_MODEL": "test-model",
    "RR_TRANSLATION_BASE_URL": "https://example.invalid/v1",
}


def make_config(**overrides) -> OpenAITranslationConfig:
    base = {
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "model": "test-model",
        # No sleeping in tests.
        "backoff_seconds": 0.0,
        "max_attempts": 3,
    }
    base.update(overrides)
    return OpenAITranslationConfig(**base)


def completion(text: str, *, prompt_tokens: int = 10, completion_tokens: int = 5):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def provider_with(handler, **overrides) -> OpenAIChatTranslationProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIChatTranslationProvider(make_config(**overrides), client=client)


class TestConfiguration:
    def test_no_key_means_no_provider(self) -> None:
        # Not a placeholder provider: a caller must be able to tell that translation is
        # unavailable, so the real-call acceptance item can be reported as PENDING.
        assert config_from_env({}) is None
        assert provider_from_env({}) is None

    def test_key_without_a_model_is_an_error_not_a_silent_default(self) -> None:
        with pytest.raises(TranslationConfigError, match="RR_TRANSLATION_MODEL"):
            config_from_env({"RR_TRANSLATION_API_KEY": "k"})

    def test_config_reads_the_environment(self) -> None:
        config = config_from_env(ENV)
        assert config is not None
        assert config.model == "test-model"
        assert config.endpoint() == "https://example.invalid/v1/chat/completions"

    def test_base_url_falls_back_to_the_documented_default_but_says_so(self) -> None:
        described = describe_configuration({"RR_TRANSLATION_API_KEY": "k", "RR_TRANSLATION_MODEL": "m"})
        assert described["base_url_is_default"] is True
        assert described["configured"] is True

    def test_describe_never_echoes_the_key(self) -> None:
        described = describe_configuration(ENV)
        assert "test-key" not in str(described)
        assert described["configured"] is True

    def test_empty_base_url_is_rejected(self) -> None:
        with pytest.raises(TranslationConfigError, match="base_url"):
            OpenAIChatTranslationProvider(make_config(base_url=""))

    def test_non_numeric_timeout_is_reported_clearly(self) -> None:
        with pytest.raises(TranslationConfigError, match="not a number"):
            config_from_env({**ENV, "RR_TRANSLATION_TIMEOUT": "soon"})

    def test_env_file_loads_and_does_not_override_the_real_environment(
        self, tmp_path, monkeypatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# a comment\nRR_TRANSLATION_MODEL=from-file\n"
            "RR_TRANSLATION_API_KEY=file-key\n\nmalformed line\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("RR_TRANSLATION_MODEL", raising=False)
        monkeypatch.setenv("RR_TRANSLATION_API_KEY", "already-set")
        loaded = load_env_file(env_file)
        assert loaded == 1  # only the unset one
        import os

        assert os.environ["RR_TRANSLATION_MODEL"] == "from-file"
        # The existing environment wins.
        assert os.environ["RR_TRANSLATION_API_KEY"] == "already-set"


class TestPromptConstruction:
    def test_language_codes_become_names(self) -> None:
        assert language_name("en") == "English"
        assert language_name("ZH") == "Chinese"
        assert language_name("xx") == "xx"

    def test_custom_prompt_with_braces_does_not_crash(self) -> None:
        # `str.format` would raise KeyError on a JSON example in the prompt, before any
        # request was sent.  Only the known placeholders are substituted.
        config = make_config(system_prompt='Translate {source_language}->{target_language}. Reply as {"t": "..."}')
        provider = OpenAIChatTranslationProvider(
            config, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=completion("x"))))
        )
        prompt = provider._system_prompt([], "en", "zh")
        assert prompt == 'Translate English->Chinese. Reply as {"t": "..."}'

    def test_glossary_is_included_when_present(self) -> None:
        provider = provider_with(lambda r: httpx.Response(200, json=completion("x")))
        prompt = provider._system_prompt([("beam", "波束"), ("gutter", "栏间空隙")], "en", "zh")
        assert "beam => 波束" in prompt
        assert "gutter => 栏间空隙" in prompt

    def test_request_body_carries_the_model_and_messages(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            seen.append(_json.loads(request.content))
            return httpx.Response(200, json=completion("译文"))

        provider = provider_with(handler)
        provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert seen[0]["model"] == "test-model"
        assert seen[0]["stream"] is False
        assert seen[0]["messages"][0]["role"] == "system"
        assert seen[0]["messages"][1]["content"] == "hello"

    def test_context_is_marked_as_not_to_be_translated(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            seen.append(_json.loads(request.content))
            return httpx.Response(200, json=completion("译文"))

        provider = provider_with(handler)
        provider.translate(
            [TranslationUnit(block_id="b1", text="hello", context="3. Method")]
        )
        user = seen[0]["messages"][1]["content"]
        assert "do not translate" in user.lower()
        assert "3. Method" in user
        assert "hello" in user


class TestSuccess:
    def test_one_unit_succeeds(self) -> None:
        provider = provider_with(lambda r: httpx.Response(200, json=completion("你好")))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.ok_count == 1
        assert outcome.results[0].text == "你好"
        assert outcome.results[0].status == "ok"
        assert outcome.is_complete

    def test_usage_is_aggregated_across_units(self) -> None:
        provider = provider_with(
            lambda r: httpx.Response(200, json=completion("x", prompt_tokens=10, completion_tokens=5))
        )
        outcome = provider.translate(
            [TranslationUnit(block_id=f"b{i}", text="hello") for i in range(3)]
        )
        assert outcome.usage["prompt_tokens"] == 30
        assert outcome.usage["completion_tokens"] == 15
        assert outcome.usage["total_tokens"] == 45
        assert outcome.usage["units"] == 3

    def test_results_come_back_in_input_order(self) -> None:
        # Concurrency means completion order is arbitrary; the caller must not have to
        # care.  The handler delays based on content so completion order is reversed.
        import time as _time

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            body = _json.loads(request.content)
            text = body["messages"][1]["content"]
            if text == "first":
                _time.sleep(0.05)
            return httpx.Response(200, json=completion(f"translated:{text}"))

        provider = provider_with(handler, max_concurrency=3)
        units = [
            TranslationUnit(block_id="a", text="first"),
            TranslationUnit(block_id="b", text="second"),
            TranslationUnit(block_id="c", text="third"),
        ]
        outcome = provider.translate(units)
        assert [r.block_id for r in outcome.results] == ["a", "b", "c"]

    def test_empty_or_blank_units_are_skipped_without_a_request(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json=completion("x"))

        provider = provider_with(handler)
        outcome = provider.translate(
            [TranslationUnit(block_id="a", text="   "), TranslationUnit(block_id="b", text="")]
        )
        assert calls == []
        assert outcome.results == []
        assert outcome.is_complete is False  # nothing measured is not a pass


class TestFailureHandling:
    def test_timeout_is_unknown_and_is_not_retried(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            raise httpx.TimeoutException("too slow", request=request)

        provider = provider_with(handler)
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        result = outcome.results[0]
        # The service may have processed it: a retry could be charged twice, and calling
        # it "failed" would be a claim we cannot support.
        assert result.status == "unknown"
        assert result.error_code == EXTERNAL_UNKNOWN_RESULT
        assert len(attempts) == 1
        assert outcome.unknown_count == 1
        assert outcome.failed_count == 0

    def test_retryable_status_is_retried_then_succeeds(self) -> None:
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(1)
            if len(seen) == 1:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, json=completion("你好"))

        provider = provider_with(handler)
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].status == "ok"
        assert outcome.results[0].attempts == 2

    def test_client_error_is_not_retried(self) -> None:
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(1)
            return httpx.Response(400, text="bad model id")

        provider = provider_with(handler)
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        result = outcome.results[0]
        assert result.status == "failed"
        assert result.error_code == ErrorCode.INVALID_REQUEST.value
        assert "bad model id" in (result.error_message or "")
        # A 400 will fail identically next time, so repeating it would just cost time.
        assert len(seen) == 1

    def test_auth_failure_is_told_apart_from_a_generic_error(self) -> None:
        # A wrong key and a malformed request need different fixes, so they must not
        # share a code.
        provider = provider_with(lambda r: httpx.Response(401, text="bad key"))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].error_code == ErrorCode.EXTERNAL_AUTH_FAILED.value

    def test_insufficient_balance_is_its_own_code(self) -> None:
        provider = provider_with(lambda r: httpx.Response(402, text="no credit"))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].error_code == ErrorCode.EXTERNAL_INSUFFICIENT_BALANCE.value

    def test_rate_limit_after_retries_reports_rate_limited(self) -> None:
        provider = provider_with(lambda r: httpx.Response(429, text="slow down"))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].error_code == ErrorCode.EXTERNAL_RATE_LIMITED.value

    def test_exhausted_retries_are_reported_as_a_failure(self) -> None:
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(1)
            return httpx.Response(503, text="unavailable")

        provider = provider_with(handler)
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].status == "failed"
        assert outcome.results[0].error_code == ErrorCode.EXTERNAL_UNAVAILABLE.value
        assert outcome.results[0].attempts == 3
        assert len(seen) == 3

    def test_transport_error_is_a_failure_after_retries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        provider = provider_with(handler)
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].status == "failed"
        assert outcome.results[0].error_code == ErrorCode.EXTERNAL_UNAVAILABLE.value

    def test_missing_choices_is_reported_not_silently_empty(self) -> None:
        provider = provider_with(lambda r: httpx.Response(200, json={"id": "x"}))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].status == "failed"
        assert outcome.results[0].error_code == ErrorCode.TRANSLATION_FAILED.value

    def test_empty_string_translation_is_a_failure(self) -> None:
        provider = provider_with(lambda r: httpx.Response(200, json=completion("   ")))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].status == "failed"
        assert outcome.results[0].error_code == ErrorCode.TRANSLATION_FAILED.value

    def test_non_json_response_is_a_failure(self) -> None:
        provider = provider_with(lambda r: httpx.Response(200, text="<html>nope</html>"))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert outcome.results[0].status == "failed"
        assert outcome.results[0].error_code == ErrorCode.TRANSLATION_FAILED.value

    def test_every_reported_code_exists_in_the_shared_contract(self) -> None:
        # The adapter must not invent a second vocabulary: a code it reports has to be
        # one the contract knows, or it cannot map to an HTTP status or be retried.
        known = {code.value for code in ErrorCode}
        for status in (400, 401, 402, 403, 404, 418, 429, 500, 502, 503, 504):
            assert code_for_status(status) in known

    def test_error_body_is_truncated(self) -> None:
        # An error body can be an entire HTML page; it must not be stored whole.
        provider = provider_with(lambda r: httpx.Response(500, text="x" * 5000))
        outcome = provider.translate([TranslationUnit(block_id="b1", text="hello")])
        assert len(outcome.results[0].error_message or "") <= 400


class TestPartialSuccess:
    def test_one_failure_does_not_lose_the_others(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            text = _json.loads(request.content)["messages"][1]["content"]
            if text == "bad":
                return httpx.Response(400, text="nope")
            return httpx.Response(200, json=completion(f"译:{text}"))

        provider = provider_with(handler, max_concurrency=1)
        units = [
            TranslationUnit(block_id="a", text="good one"),
            TranslationUnit(block_id="b", text="bad"),
            TranslationUnit(block_id="c", text="good two"),
        ]
        outcome = provider.translate(units)
        by_id = {r.block_id: r for r in outcome.results}
        assert by_id["a"].status == "ok"
        assert by_id["b"].status == "failed"
        assert by_id["c"].status == "ok"
        assert outcome.ok_count == 2
        assert outcome.failed_count == 1
        # The whole request is not a success, and saying so is the point.
        assert outcome.is_complete is False
        # The successful translations are still there to be stored.
        assert by_id["a"].text == "译:good one"

    def test_mixed_unknown_and_failed_are_counted_separately(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            text = _json.loads(request.content)["messages"][1]["content"]
            if text == "slow":
                raise httpx.TimeoutException("slow", request=request)
            return httpx.Response(500, text="boom")

        provider = provider_with(handler, max_concurrency=1)
        outcome = provider.translate(
            [
                TranslationUnit(block_id="a", text="slow"),
                TranslationUnit(block_id="b", text="broken"),
            ]
        )
        assert outcome.unknown_count == 1
        assert outcome.failed_count == 1
        assert outcome.ok_count == 0


class TestCacheFingerprint:
    def test_fingerprint_changes_with_the_model(self) -> None:
        a = make_config(model="m1").cache_fingerprint()
        b = make_config(model="m2").cache_fingerprint()
        assert a != b

    def test_fingerprint_changes_with_temperature(self) -> None:
        assert make_config(temperature=0.2).cache_fingerprint() != make_config(
            temperature=0.9
        ).cache_fingerprint()

    def test_fingerprint_carries_the_prompt_version(self) -> None:
        assert make_config().cache_fingerprint()["prompt_version"] == PROMPT_VERSION

    def test_fingerprint_excludes_credentials_and_timeouts(self) -> None:
        # These do not change what the service returns, so they must not invalidate a
        # cache and force a re-translation.
        a = make_config(api_key="key-one", timeout_seconds=10)
        b = make_config(api_key="key-two", timeout_seconds=999)
        assert a.cache_fingerprint() == b.cache_fingerprint()
        assert "test-key" not in str(a.cache_fingerprint())
