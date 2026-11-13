"""Lazy official Responses SDK boundary; no solver or verification capability."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.resources import ApiReservation, ResourceStop
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


def _credential_in_text(text: str, key: str) -> bool:
    """Recognize literal or one JSON-escape-layer echoes without exposing them.

    Decoding the bounded text directly also covers a malformed JSON container
    containing otherwise valid escaped secret text. No decoded body escapes.
    """
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
        if text[index] == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following == "u" and index + 6 <= len(text):
                try:
                    decoded.append(chr(int(text[index + 2 : index + 6], 16)))
                    index += 6
                    continue
                except ValueError:
                    pass
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
    ) -> TransportResponse | TransportFailure:
        if options.mode is not ProviderMode.OPENAI_LIVE:
            return TransportFailure(FailureCode.DISABLED, False)
        if reservation is None:
            return TransportFailure(FailureCode.RESERVATION_INVALID, False)
        try:
            reservation.validate(
                model=options.model,
                request_digest=stable_digest(response_request(prompt, options)),
                cache_reads_allowed=False,
                cache_writes_allowed=False,
            )
        except (ValueError, ResourceStop):
            return TransportFailure(FailureCode.RESERVATION_INVALID, False)
        if len(prompt.encode("utf-8")) > options.max_input_bytes:
            return TransportFailure(FailureCode.INPUT_BUDGET, False)
        try:
            sdk_spec = importlib.util.find_spec("openai")
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
            with OpenAI(
                api_key=key,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=options.timeout_seconds,
            ) as client:
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
            texts: list[str] = []
            refused = False
            unsupported = False
            if len(response.output) > 4096:
                return TransportResponse(
                    "", "invalid_output", "", False, None, unsupported_output=True
                )
            characters = 0
            for item in response.output:
                if item.type == "message":
                    if len(item.content) > 4096:
                        unsupported = True
                        break
                    for content in item.content:
                        if content.type == "refusal":
                            refused = True
                        elif content.type == "output_text":
                            characters += len(content.text)
                            if characters > options.max_response_bytes:
                                unsupported = True
                                break
                            texts.append(content.text)
                elif item.type != "reasoning":
                    unsupported = True
            text = "".join(texts)
            echo = any(
                _credential_in_text(value, key)
                for value in (text, response.model, response.status or "missing")
            )
            return TransportResponse(
                model="" if _credential_in_text(response.model, key) else response.model,
                status="missing"
                if _credential_in_text(response.status or "missing", key)
                else response.status or "missing",
                text="" if echo else text,
                refused=refused,
                usage=_token_usage(response.usage),
                unsupported_output=unsupported or len(texts) != 1,
                credential_echo=echo,
            )
        except APITimeoutError:
            return TransportFailure(FailureCode.TIMEOUT, True)
        except Exception:
            # No SDK exception text, request, response or credential enters diagnostics.
            return TransportFailure(FailureCode.TRANSPORT_FAILURE, True)
