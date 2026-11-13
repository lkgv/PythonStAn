"""Independent synthetic SDK controls at CPU, fee and credential boundaries."""

from __future__ import annotations

import importlib.machinery
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType

import pytest

from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.resources import ApiReservation, RateCard, RunLedger
from pythonstan.config.llm_provider import ProviderMode, ProviderOptions
from pythonstan.llm import openai_transport as boundary
from pythonstan.llm.openai_transport import (
    FailureCode,
    OpenAIResponsesTransport,
    TransportFailure,
    TransportResponse,
)
from pythonstan.llm.response_schema import response_request

_KEY = "synthetic-cpu-sdk-sentinel"


def _options() -> ProviderOptions:
    return ProviderOptions(
        ProviderMode.OPENAI_LIVE, timeout_seconds=1, max_output_tokens=32, max_total_tokens=128
    )


def _ledger() -> RunLedger:
    return RunLedger(
        2000,
        1,
        0,
        max_api_calls=2,
        max_api_tokens=1000,
        max_api_microusd=1000,
        rate_card=RateCard(
            "gpt-6-luna", "synthetic-rate", "synthetic:test-only", 1000000, 500000, 1000000
        ),
    )


def _permit(ledger: RunLedger, options: ProviderOptions, prompt: str) -> ApiReservation:
    return ledger.reserve_api(
        model=options.model,
        request_digest=stable_digest(response_request(prompt, options)),
        input_tokens=2,
        output_tokens=options.max_output_tokens,
        cache_reads_allowed=False,
        cache_writes_allowed=False,
    )


@dataclass
class _Details:
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    model_fields_set: set[str] = field(
        default_factory=lambda: {
            "cached_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        }
    )


@dataclass
class _Usage:
    input_tokens: int = 2
    output_tokens: int = 3
    total_tokens: int = 5
    input_tokens_details: _Details = field(default_factory=_Details)
    output_tokens_details: _Details = field(default_factory=_Details)
    model_fields_set: set[str] = field(
        default_factory=lambda: {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_details",
            "output_tokens_details",
        }
    )


@dataclass
class _Text:
    text: str
    type: str = "output_text"


@dataclass
class _Message:
    content: list[_Text]
    type: str = "message"


@dataclass
class _Receipt:
    output: list[_Message]
    model: str = "gpt-6-luna"
    status: str = "completed"
    usage: _Usage = field(default_factory=_Usage)


@dataclass
class _SDK:
    receipt: _Receipt
    after_create: Callable[[], None] | None = None
    creates: int = 0
    constructions: int = 0


def _install_sdk(monkeypatch: pytest.MonkeyPatch, sdk: _SDK) -> None:
    # Replacing the environment precedes importing the optional SDK; only this
    # synthetic key is visible even if a dependency consults its environment.
    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": _KEY})
    import openai

    class Responses:
        def create(self, **kwargs: object) -> _Receipt:
            assert kwargs["store"] is False and kwargs["tools"] == []
            sdk.creates += 1
            if sdk.after_create is not None:
                sdk.after_create()
            return sdk.receipt

    class Client:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["api_key"] == _KEY and kwargs["max_retries"] == 0
            sdk.constructions += 1
            self.responses = Responses()

        def __enter__(self) -> Client:
            return self

        def __exit__(
            self,
            kind: type[BaseException] | None,
            error: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            pass

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(
        boundary.importlib.util,
        "find_spec",
        lambda name: importlib.machinery.ModuleSpec(name, loader=None),
    )


def _forbid_discovery_and_key(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    class Environment(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str | None:
            seen.append("credential")
            raise AssertionError("Unfunded or foreign work reached a credential")

    def discovery(name: str) -> None:
        seen.append("discovery")
        raise AssertionError("Unfunded or foreign work reached SDK discovery")

    monkeypatch.setattr(boundary.os, "environ", Environment())
    monkeypatch.setattr(boundary.importlib.util, "find_spec", discovery)
    return seen


def test_zero_registered_sdk_cpu_denies_before_discovery_and_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, options, prompt = _ledger(), _options(), "synthetic request"
    permit = _permit(ledger, options, prompt)
    grant = ledger.reserve_work(0, category="sdk_cpu_probe")
    seen = _forbid_discovery_and_key(monkeypatch)
    result = OpenAIResponsesTransport().send(
        prompt, options, reservation=permit, work_reservation=grant
    )
    assert isinstance(result, TransportFailure)
    assert result.code is FailureCode.RESOURCE_BUDGET
    assert seen == [] and grant.observed == 0 and permit.status == "reserved"


def test_foreign_sdk_cpu_grant_cannot_reach_discovery_or_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, options, prompt = _ledger(), _options(), "synthetic request"
    permit = _permit(ledger, options, prompt)
    foreign = _ledger().reserve_work(100, category="sdk_cpu_probe")
    seen = _forbid_discovery_and_key(monkeypatch)
    result = OpenAIResponsesTransport().send(
        prompt, options, reservation=permit, work_reservation=foreign
    )
    assert isinstance(result, TransportFailure)
    assert result.code is FailureCode.RESERVATION_INVALID
    assert seen == [] and foreign.observed == 0 and permit.status == "reserved"


def test_sibling_scope_grants_cannot_reach_sdk_or_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, options, prompt = _ledger(), _options(), "synthetic request"
    permit = _permit(ledger, options, prompt)
    grant = ledger.reserve_work(100, category="sdk_cpu_probe")
    seen = _forbid_discovery_and_key(monkeypatch)
    with ledger.work_scope(0, "later_scope"):
        result = OpenAIResponsesTransport().send(
            prompt, options, reservation=permit, work_reservation=grant
        )
    assert isinstance(result, TransportFailure)
    assert result.code is FailureCode.RESERVATION_INVALID
    assert seen == [] and grant.observed == 0 and permit.status == "reserved"


def test_known_usage_settles_before_first_postresponse_cpu_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, options, prompt = _ledger(), _options(), "synthetic request"
    permit = _permit(ledger, options, prompt)
    grant = ledger.reserve_work(2000, category="sdk_cpu_probe")
    sdk = _SDK(_Receipt([_Message([_Text("{}")])]))
    sdk.after_create = lambda: grant.consume(grant.held, owner=ledger)
    _install_sdk(monkeypatch, sdk)
    result = OpenAIResponsesTransport().send(
        prompt, options, reservation=permit, work_reservation=grant
    )
    assert sdk.creates == 1
    assert isinstance(result, TransportFailure)
    assert result.code is FailureCode.RESOURCE_BUDGET
    assert permit.status == "settled", "A complete matching-model receipt must retain known fees"
    assert ledger.api_consumed_tokens == 5 and ledger.api_consumed_money == 5
    assert ledger.work.used + ledger.work.reserved <= 2000


@pytest.mark.parametrize("escaped", [False, True])
def test_credential_redaction_retains_known_usage_without_leaking_text(
    monkeypatch: pytest.MonkeyPatch,
    escaped: bool,
) -> None:
    ledger, options, prompt = _ledger(), _options(), "synthetic request"
    permit = _permit(ledger, options, prompt)
    secret = "".join(f"\\u{ord(character):04x}" for character in _KEY) if escaped else _KEY
    sdk = _SDK(_Receipt([_Message([_Text('{"field":"' + secret + '"}')])]))
    _install_sdk(monkeypatch, sdk)
    result = OpenAIResponsesTransport().send(prompt, options, reservation=permit)
    assert isinstance(result, TransportResponse)
    assert result.credential_echo and result.text == ""
    assert _KEY not in repr(result)
    assert permit.status == "settled"
    assert ledger.api_consumed_tokens == 5 and ledger.api_consumed_money == 5
