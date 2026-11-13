"""Offline transport and adversarial wire controls; these tests make no API calls."""

from __future__ import annotations

import builtins
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pythonstan import Source, analyze
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_object,
    validate_json_object,
)
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.config.llm_provider import ProviderMode, ProviderOptions
from pythonstan.llm.openai_transport import (
    FailureCode,
    OpenAIResponsesTransport,
    TokenUsage,
    TransportFailure,
    TransportResponse,
)
from pythonstan.llm.providers import OfflineDispatchProposer, make_summary_provider
from pythonstan.llm.responses import ResponsesCandidateProposer

if TYPE_CHECKING:
    from types import ModuleType

SOURCE = """def identity(payload):
    return payload
def dispatch(key, payload, unused):
    return TABLE[key](payload)
TABLE = {'read': identity}
"""
CANARY = "synthetic-canary-not-a-credential"


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.failure: TransportFailure | None = None
        self.change: str = ""

    def send(self, prompt: str, options: ProviderOptions) -> TransportResponse | TransportFailure:
        self.calls += 1
        if self.failure is not None:
            return self.failure
        request = validate_json_object(json.loads(prompt.split("\n", 1)[1]))
        proposal = OfflineDispatchProposer().propose(SOURCE)
        candidate = validate_json_object(proposal.candidate)
        candidate["identity"] = request["identity"]
        text = json.dumps(candidate)
        if self.change == "duplicate":
            text = text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
        elif self.change == "stale":
            require_object(candidate["identity"])["state_version"] = "stale"
            text = json.dumps(candidate)
        elif self.change == "authority":
            candidate["verified"] = True
            text = json.dumps(candidate)
        elif self.change == "depth":
            text = "[" * 1000 + "0" + "]" * 1000
        elif self.change == "large":
            text = " " * (options.max_response_bytes + 1)
        elif self.change == "malformed":
            text = "{" + CANARY
        return TransportResponse(
            model="other-model" if self.change == "model" else options.model,
            status="incomplete" if self.change == "incomplete" else "completed",
            text=text,
            refused=self.change == "refusal",
            usage=(
                None
                if self.change == "usage"
                else TokenUsage(3, 2, 6 if self.change == "usage_invalid" else 5)
            ),
            unsupported_output=self.change == "tools",
        )


def proposer(transport: FakeTransport, **changes: int) -> ResponsesCandidateProposer:
    options = replace(ProviderOptions(ProviderMode.OPENAI_LIVE), **changes)
    return ResponsesCandidateProposer(options, policy_digest="test-policy", transport=transport)


def test_valid_wire_is_only_an_untrusted_candidate() -> None:
    transport = FakeTransport()
    provider = proposer(transport)
    proposal = provider.propose(SOURCE)
    assert proposal.status == "candidate"
    candidate = validate_json_object(proposal.candidate)
    assert "verified" not in candidate and "proof" not in candidate
    assert require_object(candidate["identity"])["policy_digest"] == "test-policy"
    assert provider.total_tokens == 5
    assert proposal.provider == "openai_live"
    assert transport.calls == 1
    assert provider.propose(SOURCE).diagnostics == (FailureCode.CALL_BUDGET.value,)
    assert transport.calls == 1


@pytest.mark.parametrize(
    ("mutation", "expected", "stopped"),
    [
        ("refusal", FailureCode.REFUSAL, False),
        ("incomplete", FailureCode.INCOMPLETE, False),
        ("usage", FailureCode.MISSING_USAGE, True),
        ("usage_invalid", FailureCode.INVALID_USAGE, True),
        ("model", FailureCode.WRONG_MODEL, True),
        ("tools", FailureCode.INVALID_OUTPUT, False),
        ("duplicate", FailureCode.INVALID_CANDIDATE, False),
        ("stale", FailureCode.INVALID_CANDIDATE, False),
        ("authority", FailureCode.INVALID_CANDIDATE, False),
        ("depth", FailureCode.INVALID_CANDIDATE, False),
        ("large", FailureCode.INVALID_CANDIDATE, False),
        ("malformed", FailureCode.INVALID_CANDIDATE, False),
    ],
)
def test_adversarial_wire_fails_closed(mutation: str, expected: FailureCode, stopped: bool) -> None:
    transport = FakeTransport()
    transport.change = mutation
    provider = proposer(transport, max_calls=2)
    proposal = provider.propose(SOURCE)
    assert proposal.candidate is None
    assert proposal.diagnostics == (expected.value,)
    assert CANARY not in repr(proposal)
    assert provider.stopped is stopped
    if stopped:
        assert provider.propose(SOURCE).diagnostics == (FailureCode.STOPPED.value,)
        assert transport.calls == 1


def test_uncertain_charge_latches_and_preserves_redacted_reason() -> None:
    transport = FakeTransport()
    transport.failure = TransportFailure(FailureCode.TIMEOUT, True)
    provider = proposer(transport, max_calls=2)
    assert provider.propose(SOURCE).diagnostics == (FailureCode.TIMEOUT.value,)
    assert provider.propose(SOURCE).diagnostics == (FailureCode.STOPPED.value,)
    assert transport.calls == 1


def test_source_and_local_work_budgets_prevent_transport() -> None:
    transport = FakeTransport()
    provider = proposer(transport, max_input_bytes=10)
    assert provider.propose(SOURCE).diagnostics == (FailureCode.INPUT_BUDGET.value,)
    provider = proposer(transport)
    assert provider.propose(SOURCE, max_work=0).status == "budget_exhausted"
    assert transport.calls == 0


def test_known_token_budget_exhaustion_stops_further_requests() -> None:
    transport = FakeTransport()
    provider = proposer(transport, max_output_tokens=1, max_total_tokens=4, max_calls=2)
    assert provider.propose(SOURCE).diagnostics == (FailureCode.TOKEN_BUDGET.value,)
    assert provider.total_tokens == 5
    assert provider.propose(SOURCE).diagnostics == (FailureCode.STOPPED.value,)
    assert transport.calls == 1


def test_transport_exception_text_never_enters_a_proposal() -> None:
    class ExplodingTransport:
        def send(
            self, prompt: str, options: ProviderOptions
        ) -> TransportResponse | TransportFailure:
            raise RuntimeError(CANARY)

    provider = ResponsesCandidateProposer(
        ProviderOptions(ProviderMode.OPENAI_LIVE),
        policy_digest="test-policy",
        transport=ExplodingTransport(),
    )
    result = provider.propose(SOURCE)
    assert result.diagnostics == (FailureCode.TRANSPORT_FAILURE.value,)
    assert CANARY not in repr(result)


def test_missing_sdk_checks_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import pythonstan.llm.openai_transport as boundary

    def absent(name: str) -> None:
        return None

    class ForbiddenEnvironment(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str | None:
            raise AssertionError("credential environment must remain unread")

    monkeypatch.setattr(boundary.importlib.util, "find_spec", absent)
    monkeypatch.setattr(boundary.os, "environ", ForbiddenEnvironment())
    result = OpenAIResponsesTransport().send(
        "synthetic input", ProviderOptions(ProviderMode.OPENAI_LIVE)
    )
    assert result == TransportFailure(FailureCode.SDK_UNAVAILABLE, False)


def test_broken_sdk_discovery_is_typed_and_reads_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pythonstan.llm.openai_transport as boundary

    def broken_spec(name: str) -> None:
        raise ValueError(CANARY)

    class ForbiddenEnvironment(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str | None:
            raise AssertionError("broken discovery must not inspect credentials")

    monkeypatch.setattr(boundary.importlib.util, "find_spec", broken_spec)
    monkeypatch.setattr(boundary.os, "environ", ForbiddenEnvironment())
    result = OpenAIResponsesTransport().send(
        "synthetic input", ProviderOptions(ProviderMode.OPENAI_LIVE)
    )
    assert result == TransportFailure(FailureCode.SDK_UNAVAILABLE, False)
    assert CANARY not in repr(result)


def test_broken_sdk_import_is_typed_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    import pythonstan.llm.openai_transport as boundary

    original_import = builtins.__import__

    def broken_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "openai" or name.startswith("openai."):
            raise ImportError(CANARY)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": CANARY})
    monkeypatch.setattr(builtins, "__import__", broken_import)
    result = OpenAIResponsesTransport().send(
        "synthetic input", ProviderOptions(ProviderMode.OPENAI_LIVE)
    )
    assert result == TransportFailure(FailureCode.SDK_UNAVAILABLE, False)
    assert CANARY not in repr(result)


@pytest.mark.parametrize("mode", [ProviderMode.DISABLED, ProviderMode.OFFLINE, ProviderMode.REPLAY])
def test_direct_nonlive_transport_checks_no_sdk_or_credentials(
    mode: ProviderMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pythonstan.llm.openai_transport as boundary

    def forbidden_lookup(name: str) -> None:
        raise AssertionError("nonlive transport inspected the SDK")

    monkeypatch.setattr(boundary.importlib.util, "find_spec", forbidden_lookup)
    result = OpenAIResponsesTransport().send("synthetic input", ProviderOptions(mode))
    assert result == TransportFailure(FailureCode.DISABLED, False)


def test_missing_credentials_is_blocked_without_sdk_import(monkeypatch: pytest.MonkeyPatch) -> None:
    import pythonstan.llm.openai_transport as boundary

    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "openai" or name.startswith("openai."):
            raise AssertionError("SDK import must not happen without credentials")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(boundary.os, "environ", {})
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = OpenAIResponsesTransport().send(
        "synthetic input", ProviderOptions(ProviderMode.OPENAI_LIVE)
    )
    assert result == TransportFailure(FailureCode.CREDENTIALS_UNAVAILABLE, False)


def test_disabled_analysis_imports_no_sdk_or_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import pythonstan.llm.openai_transport as boundary

    sdk_imports: list[str] = []
    key_lookups: list[str] = []
    transport_calls: list[str] = []
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "openai" or name.startswith("openai."):
            sdk_imports.append(name)
            raise AssertionError("disabled execution imported the SDK")
        return original_import(name, globals, locals, fromlist, level)

    def forbidden_send(
        self: OpenAIResponsesTransport, prompt: str, options: ProviderOptions
    ) -> TransportResponse | TransportFailure:
        transport_calls.append(prompt)
        raise AssertionError("disabled execution reached transport")

    monkeypatch.setattr(OpenAIResponsesTransport, "send", forbidden_send)

    # This mapping has no credential; the execution itself must not inspect it.
    class Environment(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str | None:
            if key == "OPENAI_API_KEY":
                key_lookups.append(key)
                raise AssertionError("disabled execution looked for a credential")
            return super().get(key, default)

    monkeypatch.setattr(boundary.os, "environ", Environment())
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = analyze(
        Source("answer = 7", module="off"),
        config={"engine": "ai_primary", "llm": {"enabled": False, "provider": "openai_live"}},
    )
    assert result.solver_status == "converged"
    assert make_summary_provider({}, {"llm": {"enabled": False}}) is None
    assert sdk_imports == []
    assert key_lookups == []
    assert transport_calls == []


@pytest.mark.parametrize("provider", ["disabled", "offline", "replay", "recorded", "adversarial"])
def test_provider_modes_have_explicit_config_identity(provider: str) -> None:
    config = resolve_config(config={"llm": {"provider": provider}})
    assert config.llm.provider == provider
    assert config.llm.provider_options.mode is ProviderMode.DISABLED


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": True, "level2": True, "provider": "disabled"},
        {"enabled": True, "level1": True, "provider": "openai_live"},
        {"provider": "openai_live", "model": "unregistered"},
        {"max_calls": 0},
        {"max_json_depth": 65},
        {"timeout_seconds": 0},
        {"api_key": CANARY},
        {"base_url": "https://unregistered.invalid"},
    ],
)
def test_provider_settings_reject_unsupported_or_secret_fields(settings: JsonObject) -> None:
    with pytest.raises((ConfigurationError, TypeError, ValueError)):
        resolve_config(config={"llm": settings})


def test_sdk_request_contract_uses_official_endpoint_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2
    import openai

    import pythonstan.llm.openai_transport as boundary

    requests: list[JsonObject] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer " + CANARY
        requests.append(validate_json_object(json.loads(request.content)))
        return httpx2.Response(
            200,
            json={
                "id": "synthetic-response",
                "object": "response",
                "created_at": 0,
                "model": "gpt-6-luna",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "synthetic-message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "{}"}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        )

    original = openai.OpenAI

    def client(*, api_key: str, base_url: str, max_retries: int, timeout: float) -> openai.OpenAI:
        assert max_retries == 0 and timeout == 30.0
        return original(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": CANARY})
    monkeypatch.setattr(openai, "OpenAI", client)
    result = OpenAIResponsesTransport().send(
        "synthetic input", ProviderOptions(ProviderMode.OPENAI_LIVE)
    )
    assert isinstance(result, TransportResponse)
    assert result.usage == TokenUsage(3, 2, 5)
    request = requests[0]
    assert request["store"] is False and request["stream"] is False
    assert request["tools"] == [] and request["model"] == "gpt-6-luna"
    text_format = require_object(require_object(request["text"])["format"])
    assert text_format["strict"] is True and text_format["type"] == "json_schema"
    assert CANARY not in json.dumps(request)


def test_public_provider_records_blocked_status_without_offline_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pythonstan.inputs import snapshot_target

    def blocked(
        self: OpenAIResponsesTransport, prompt: str, options: ProviderOptions
    ) -> TransportFailure:
        return TransportFailure(FailureCode.SDK_UNAVAILABLE, False)

    monkeypatch.setattr(OpenAIResponsesTransport, "send", blocked)
    target = Source(SOURCE, module="provider_input")
    config = resolve_config(
        target,
        {
            "engine": "ai_primary",
            "llm": {"enabled": True, "level2": True, "provider": "openai_live"},
        },
    )
    provider = make_summary_provider(snapshot_target(target, config), config)
    assert provider is not None
    records = provider.proof_records
    assert any(record.get("provider_status") == "blocked" for record in records)
    assert any(record.get("provider_diagnostics") == ("sdk_unavailable",) for record in records)
    assert all(record.get("status") != "verified" for record in records)
    assert CANARY not in repr(records)
