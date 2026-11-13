"""Lazy official Responses SDK boundary; no solver or verification capability."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.resources import (
    ApiReservation,
    ResourceStop,
    RunLedger,
    WorkReservation,
)
from pythonstan.config.llm_provider import ProviderMode, ProviderOptions

from .response_schema import candidate_schema, response_request

if TYPE_CHECKING:
    from openai.types.responses.response_usage import ResponseUsage


class FailureCode(StrEnum):
    DISABLED = "transport_not_enabled"
    SDK_UNAVAILABLE = "sdk_unavailable"
    CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
    TRANSPORT_FAILURE = "transport_failure"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    INCOMPLETE = "incomplete"
    MISSING_USAGE = "missing_usage"
    INVALID_USAGE = "invalid_usage"
    WRONG_MODEL = "wrong_model"
    INVALID_OUTPUT = "invalid_output"
    INVALID_CANDIDATE = "invalid_candidate"
    INPUT_BUDGET = "input_budget"
    CALL_BUDGET = "call_budget"
    TOKEN_BUDGET = "token_budget"
    STOPPED = "stopped_after_uncertain_charge"
    RATE_CARD_UNAVAILABLE = "rate_card_unavailable"
    TOKEN_QUOTE_UNAVAILABLE = "token_quote_unavailable"
    RESERVATION_INVALID = "invalid_request_reservation"
    RESOURCE_BUDGET = "request_resource_budget"
    MONEY_BUDGET = "money_budget"
    CREDENTIAL_ECHO = "credential_echo_redacted"
    COST_DETAILS_UNAVAILABLE = "cost_breakdown_unavailable"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None = 0
    reasoning_tokens: int | None = 0
    cache_write_tokens: int | None = 0
    details_complete: bool = True


@dataclass(frozen=True)
class TransportResponse:
    model: str
    status: str
    text: str
    refused: bool
    usage: TokenUsage | None
    unsupported_output: bool = False
    credential_echo: bool = False


@dataclass(frozen=True)
class TransportFailure:
    code: FailureCode
    charge_uncertain: bool


class ResponsesTransport(Protocol):
    def send(
        self,
        prompt: str,
        options: ProviderOptions,
        *,
        reservation: ApiReservation | None = None,
    ) -> TransportResponse | TransportFailure: ...


def _usage_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _token_usage(usage: ResponseUsage | None) -> TokenUsage | None:
    """Retain whole usage when a vendor omits optional detail fields."""
    if usage is None:
        return None
    inputs = (
        usage.input_tokens_details if "input_tokens_details" in usage.model_fields_set else None
    )
    outputs = (
        usage.output_tokens_details if "output_tokens_details" in usage.model_fields_set else None
    )
    cached = (
        None
        if inputs is None or "cached_tokens" not in inputs.model_fields_set
        else inputs.cached_tokens
    )
    writes = (
        None
        if inputs is None or "cache_write_tokens" not in inputs.model_fields_set
        else inputs.cache_write_tokens
    )
    reasoning = (
        None
        if outputs is None or "reasoning_tokens" not in outputs.model_fields_set
        else outputs.reasoning_tokens
    )
    return TokenUsage(
        _usage_count(usage.input_tokens if "input_tokens" in usage.model_fields_set else None),
        _usage_count(usage.output_tokens if "output_tokens" in usage.model_fields_set else None),
        _usage_count(usage.total_tokens if "total_tokens" in usage.model_fields_set else None),
        _usage_count(cached),
        _usage_count(reasoning),
        _usage_count(writes),
        details_complete=all(type(value) is int for value in (cached, reasoning, writes)),
    )


def _retire_sdk_usage(
    usage: TokenUsage | None, model: str, expected_model: str, reservation: ApiReservation
) -> None:
    if (
        usage is not None
        and model == expected_model
        and type(usage.input_tokens) is int
        and usage.input_tokens is not None
        and type(usage.output_tokens) is int
        and usage.output_tokens is not None
        and type(usage.total_tokens) is int
        and usage.input_tokens + usage.output_tokens == usage.total_tokens
    ):
        with suppress(ValueError):
            reservation.settle(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_input_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                reasoning_tokens=usage.reasoning_tokens,
            )


def _credential_in_text(text: str, key: str, *, visit: Callable[[int], None] | None = None) -> bool:
    """Recognize literal or one JSON-escape-layer echoes without exposing them.

    Decoding the bounded text directly also covers a malformed JSON container
    containing otherwise valid escaped secret text. No decoded body escapes.
    """
    if visit is not None:
        visit(len(text) + 1)
    if key in text:
        return True
    decoded: list[str] = []
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    index = 0
    while index < len(text):
        if visit is not None:
            visit(1)
        if text[index] == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following == "u" and index + 6 <= len(text):
                with suppress(ValueError):
                    codepoint = int(text[index + 2 : index + 6], 16)
                    width = 6
                    if (
                        0xD800 <= codepoint <= 0xDBFF
                        and index + 12 <= len(text)
                        and text[index + 6 : index + 8] == "\\u"
                    ):
                        low = int(text[index + 8 : index + 12], 16)
                        if 0xDC00 <= low <= 0xDFFF:
                            codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00
                            width = 12
                    decoded.append(chr(codepoint))
                    index += width
                    continue
            elif following in escapes:
                decoded.append(escapes[following])
                index += 2
                continue
        decoded.append(text[index])
        index += 1
    return key in "".join(decoded)


class OpenAIResponsesTransport:
    """Construction is inert; importing the SDK and reading the key happen in send."""

    def send(
        self,
        prompt: str,
        options: ProviderOptions,
        *,
        reservation: ApiReservation | None = None,
        work_reservation: WorkReservation | None = None,
        work_ceiling: int | None = None,
    ) -> TransportResponse | TransportFailure:
        if options.mode is not ProviderMode.OPENAI_LIVE:
            return TransportFailure(FailureCode.DISABLED, False)
        if reservation is None:
            return TransportFailure(FailureCode.RESERVATION_INVALID, False)
        if work_ceiling is not None and (type(work_ceiling) is not int or work_ceiling < 0):
            return TransportFailure(FailureCode.RESERVATION_INVALID, False)
        grant = work_reservation
        try:
            reservation.validate(
                model=options.model,
                request_digest=reservation.request_digest,
                cache_reads_allowed=False,
                cache_writes_allowed=False,
            )
            ledger = reservation.resource_ledger
            if grant is None:
                grant = ledger.reserve_work(ledger.remaining_work, category="responses_sdk_cpu")
            grant.consume(0, owner=ledger)
            ceiling = grant.observed + grant.held if work_ceiling is None else work_ceiling

            def spend(amount: int) -> None:
                grant.consume(amount, owner=ledger, ceiling=ceiling)

            spend(1)
            return self._send(prompt, options, reservation=reservation, ledger=ledger, spend=spend)
        except ResourceStop:
            return TransportFailure(
                FailureCode.RESOURCE_BUDGET, reservation.status in {"dispatched", "unknown"}
            )
        except ValueError:
            return TransportFailure(FailureCode.RESERVATION_INVALID, False)
        finally:
            if work_reservation is None and grant is not None and grant.status == "reserved":
                grant.settle(grant.observed)

    def _send(
        self,
        prompt: str,
        options: ProviderOptions,
        *,
        reservation: ApiReservation,
        ledger: RunLedger,
        spend: Callable[[int], None],
    ) -> TransportResponse | TransportFailure:
        with ledger.structural_meter("sdk_request", charge=spend) as meter:
            visit_json_text(prompt, meter.visit)
        request = response_request(prompt, options)
        ledger.charge_json(request, "sdk_request_digest", charge=spend)
        try:
            reservation.validate(
                model=options.model,
                request_digest=stable_digest(request),
                cache_reads_allowed=False,
                cache_writes_allowed=False,
            )
        except (ValueError, ResourceStop):
            return TransportFailure(FailureCode.RESERVATION_INVALID, False)
        if len(prompt.encode("utf-8")) > options.max_input_bytes:
            return TransportFailure(FailureCode.INPUT_BUDGET, False)
        try:
            spend(1)
            sdk_spec = importlib.util.find_spec("openai")
        except ResourceStop:
            raise
        except Exception:
            return TransportFailure(FailureCode.SDK_UNAVAILABLE, False)
        if sdk_spec is None:
            return TransportFailure(FailureCode.SDK_UNAVAILABLE, False)
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return TransportFailure(FailureCode.CREDENTIALS_UNAVAILABLE, False)
        # SDK imports stay below both explicit-live selection and credential checks.
        try:
            from openai import APITimeoutError, OpenAI
        except Exception:
            return TransportFailure(FailureCode.SDK_UNAVAILABLE, False)

        try:
            spend(1)
            with OpenAI(
                api_key=key,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=options.timeout_seconds,
            ) as client:
                # Admit bounded usage reconciliation before dispatch. A valid
                # report must retire its fee even if output work then stops.
                spend(1)
                reservation.dispatch()
                response = client.responses.create(
                    model=options.model,
                    input=prompt,
                    store=False,
                    stream=False,
                    tools=[],
                    prompt_cache_options={"mode": "explicit"},
                    max_output_tokens=options.max_output_tokens,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "pythonstan_dispatch_candidate",
                            "strict": True,
                            "schema": dict(candidate_schema()),
                        }
                    },
                )
                usage = _token_usage(response.usage)
                _retire_sdk_usage(usage, response.model, options.model, reservation)
            texts: list[str] = []
            refused = False
            unsupported = False
            if len(response.output) > 4096:
                return TransportResponse(
                    response.model if response.model == options.model else "",
                    "invalid_output",
                    "",
                    False,
                    usage,
                    unsupported_output=True,
                )
            characters = 0
            for item in response.output:
                spend(1)
                if item.type == "message":
                    if len(item.content) > 4096:
                        unsupported = True
                        break
                    for content in item.content:
                        spend(1)
                        if content.type == "refusal":
                            refused = True
                        elif content.type == "output_text":
                            characters += len(content.text)
                            if characters > options.max_response_bytes:
                                unsupported = True
                                break
                            with ledger.structural_meter("sdk_text_copy", charge=spend) as meter:
                                visit_json_text(content.text, meter.visit)
                            texts.append(content.text)
                elif item.type != "reasoning":
                    unsupported = True
            text = "".join(texts)
            with ledger.structural_meter("sdk_credential_redaction", charge=spend) as meter:
                echo = any(
                    _credential_in_text(value, key, visit=meter.visit)
                    for value in (text, response.model, response.status or "missing")
                )
                model_echo = _credential_in_text(response.model, key, visit=meter.visit)
                status_echo = _credential_in_text(
                    response.status or "missing", key, visit=meter.visit
                )
            return TransportResponse(
                model="" if model_echo else response.model,
                status="missing" if status_echo else response.status or "missing",
                text="" if echo else text,
                refused=refused,
                usage=usage,
                unsupported_output=unsupported or len(texts) != 1,
                credential_echo=echo,
            )
        except ResourceStop:
            raise
        except APITimeoutError:
            return TransportFailure(
                FailureCode.TIMEOUT, reservation.status in {"dispatched", "unknown"}
            )
        except Exception:
            # No SDK exception text, request, response or credential enters diagnostics.
            return TransportFailure(
                FailureCode.TRANSPORT_FAILURE, reservation.status in {"dispatched", "unknown"}
            )
