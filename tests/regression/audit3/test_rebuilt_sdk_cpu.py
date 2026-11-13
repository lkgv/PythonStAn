"""The concrete SDK boundary pays finite fuel before credentials and DTO scans."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.resources import (
    ApiReservation,
    RateCard,
    ResourceStop,
    RunLedger,
)
from pythonstan.config.llm_provider import ProviderMode, ProviderOptions
from pythonstan.llm.openai_transport import (
    FailureCode,
    OpenAIResponsesTransport,
    TransportFailure,
    _credential_in_text,
)
from pythonstan.llm.response_schema import response_request
from pythonstan.llm.responses import InputTokenQuote, ResponsesCandidateProposer


def ledger(limit: int) -> RunLedger:
    return RunLedger(
        limit,
        0,
        0,
        memory_reader=lambda: 0,
        max_api_calls=1,
        max_api_tokens=20,
        max_api_microusd=20,
        rate_card=RateCard(
            "gpt-6-luna", "synthetic", "synthetic:test-only", 1000000, 500000, 1000000
        ),
    )


def permit(run: RunLedger, prompt: str, options: ProviderOptions) -> ApiReservation:
    return run.reserve_api(
        model=options.model,
        request_digest=stable_digest(response_request(prompt, options)),
        input_tokens=3,
        output_tokens=7,
        cache_reads_allowed=False,
        cache_writes_allowed=False,
    )


def test_zero_sdk_fuel_blocks_before_even_the_local_key_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pythonstan.llm.openai_transport as boundary

    class NoCredentials(dict[str, str]):
        def get(self, name: str, default: str | None = None) -> str | None:
            if name == "OPENAI_API_KEY":
                raise AssertionError("credential lookup without admitted CPU work")
            return super().get(name, default)

    run = ledger(0)
    options = ProviderOptions(ProviderMode.OPENAI_LIVE)
    grant = permit(run, "synthetic input", options)
    monkeypatch.setattr(boundary.os, "environ", NoCredentials())
    result = OpenAIResponsesTransport().send("synthetic input", options, reservation=grant)
    assert result == TransportFailure(FailureCode.RESOURCE_BUDGET, False)
    assert run.work.used == run.work.reserved == 0 and grant.status == "reserved"


def test_known_usage_is_settled_before_a_large_sdk_output_scan_exhausts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2
    import openai

    import pythonstan.llm.openai_transport as boundary

    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "synthetic",
                "object": "response",
                "created_at": 0,
                "model": "gpt-6-luna",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "id": f"r{index}", "summary": []} for index in range(2000)
                ],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
            },
        )

    original = openai.OpenAI

    def client(*, api_key: str, base_url: str, max_retries: int, timeout: float) -> openai.OpenAI:
        return original(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": "synthetic-not-a-key"})
    monkeypatch.setattr(openai, "OpenAI", client)
    run = ledger(500)
    options = ProviderOptions(ProviderMode.OPENAI_LIVE)
    grant = permit(run, "synthetic input", options)
    result = OpenAIResponsesTransport().send("synthetic input", options, reservation=grant)
    assert result == TransportFailure(FailureCode.RESOURCE_BUDGET, False)
    assert len(requests) == 1 and grant.status == "settled"
    assert run.api_consumed_tokens == run.api_consumed_money == 5
    assert run.api_reserved_tokens == run.api_reserved_money == 0
    assert run.work.used == 500 and run.work.reserved == 0


def test_json_surrogate_pairs_are_recognized_as_valid_unicode_credential_echoes() -> None:
    canary = "synthetic-😀-canary"
    assert _credential_in_text(json.dumps({"echo": canary}), canary)
    assert _credential_in_text('{"malformed":' + json.dumps(canary), canary)


def test_lower_memory_scope_enforces_before_the_child_stage_and_does_not_latch_global_stop() -> (
    None
):
    run = RunLedger(100, 0, 10, memory_reader=lambda: 1024 * 1024)
    entered = []
    with (
        pytest.raises(ResourceStop, match="stage_max_memory_mb"),
        run.work_scope(100, "lower-memory", max_memory_mb=0.5),
    ):
        entered.append(True)
    assert not entered and run.stop_reason is None
    assert run.remaining_memory_bytes == 9 * 1024 * 1024
    assert run.memory_checks == run.memory_samples == 2


def test_scope_memory_caps_include_real_child_observations_without_enabling_global_cap() -> None:
    run = RunLedger(100, 0, 0, memory_reader=lambda: 1024 * 1024)
    with run.work_scope(100, "child", max_memory_mb=2):
        assert run.remaining_memory_bytes == 1024 * 1024
        with pytest.raises(ResourceStop, match="stage_max_memory_mb"):
            run.record_child_memory(2 * 1024 * 1024)
        run.release_child_memory()
    assert run.stop_reason is None and run.remaining_memory_bytes is None
    assert run.memory_checks == 3 and run.memory_samples == 4


def test_direct_driver_keeps_the_actual_memory_sample_when_the_first_check_raises() -> None:
    from pythonstan.analysis.contracts import EntryScenario, SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver

    run = RunLedger(100, 0, 1, memory_reader=lambda: 2 * 1024 * 1024)
    driver = InterproceduralDriver(
        (SourceModule("example", "example.py", "result = 17\n"),),
        resource_ledger=run,
    )
    result = driver.run((EntryScenario("example"),)).to_dict()
    assert result["solver_status"] == "budget_exhausted"
    assert result["stats"]["work"] == 0
    assert result["stats"]["memory_checks"] == result["stats"]["memory_samples"] == 1
    assert result["stats"]["resident_memory_bytes"] == 2 * 1024 * 1024
    assert run.stop_reason == "max_memory_mb"


def test_sdk_cleanup_exception_keeps_the_already_reported_known_fee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai
    from openai.types.responses import Response

    import pythonstan.llm.openai_transport as boundary

    class Responses:
        def create(self, **kwargs: object) -> Response:
            return Response.model_validate(
                {
                    "id": "synthetic",
                    "object": "response",
                    "created_at": 0,
                    "model": "gpt-6-luna",
                    "status": "completed",
                    "output": [],
                    "parallel_tool_calls": False,
                    "tool_choice": "none",
                    "tools": [],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                }
            )

    class Client:
        responses = Responses()

        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": "synthetic-not-a-key"})
    monkeypatch.setattr(openai, "OpenAI", Client)
    run = ledger(2000)
    options = ProviderOptions(ProviderMode.OPENAI_LIVE)
    grant = permit(run, "synthetic input", options)
    result = OpenAIResponsesTransport().send("synthetic input", options, reservation=grant)
    assert grant.status == "settled"
    assert run.api_consumed_tokens == run.api_consumed_money == 5
    assert run.api_reserved_tokens == run.api_reserved_money == 0
    assert result == TransportFailure(FailureCode.TRANSPORT_FAILURE, False)


def test_sdk_constructor_failure_is_truthfully_undispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai

    import pythonstan.llm.openai_transport as boundary

    def constructor(**kwargs: object) -> None:
        raise RuntimeError("synthetic constructor failure")

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": "synthetic-not-a-key"})
    monkeypatch.setattr(openai, "OpenAI", constructor)
    run = ledger(2000)
    options = ProviderOptions(ProviderMode.OPENAI_LIVE)
    grant = permit(run, "synthetic input", options)
    result = OpenAIResponsesTransport().send("synthetic input", options, reservation=grant)
    assert result == TransportFailure(FailureCode.TRANSPORT_FAILURE, False)
    assert grant.status == "reserved" and run.api_consumed_money == 0


@pytest.mark.parametrize("local_limit", [1, 200])
def test_hosted_sdk_grant_cannot_bypass_the_proposer_local_ceiling(
    monkeypatch: pytest.MonkeyPatch, local_limit: int
) -> None:
    import openai
    from openai.types.responses import Response

    import pythonstan.llm.openai_transport as boundary

    calls: list[bool] = []

    class Counter:
        def quote(self, request_digest: str, model: str, request_body: str) -> InputTokenQuote:
            return InputTokenQuote(request_digest, model, 3)

    class Responses:
        def create(self, **kwargs: object) -> Response:
            calls.append(True)
            return Response.model_validate(
                {
                    "id": "synthetic",
                    "object": "response",
                    "created_at": 0,
                    "model": "gpt-6-luna",
                    "status": "completed",
                    "parallel_tool_calls": False,
                    "tool_choice": "none",
                    "tools": [],
                    "output": [
                        {"type": "reasoning", "id": f"r{i}", "summary": []} for i in range(2000)
                    ],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                }
            )

    class Client:
        responses = Responses()

        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": "synthetic-not-a-key"})
    monkeypatch.setattr(openai, "OpenAI", Client)
    run = ledger(2000)
    options = replace(ProviderOptions(ProviderMode.OPENAI_LIVE), max_output_tokens=7)
    grant = run.reserve_work(2000, category="responses_proposer_cpu")
    proposer = ResponsesCandidateProposer(
        options, policy_digest="synthetic", resource_ledger=run, token_counter=Counter()
    )
    result = proposer.propose("pass\n", max_work=local_limit, work_reservation=grant)
    assert result.candidate is None
    assert grant.observed == run.work.used <= local_limit
    if local_limit == 1:
        assert not calls and run.api_calls == 0
