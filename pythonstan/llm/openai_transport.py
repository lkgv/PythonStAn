"""Lazy official Responses SDK boundary; no solver or verification capability."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pythonstan.config.llm_provider import ProviderMode, ProviderOptions

from .response_schema import candidate_schema


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


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class TransportResponse:
    model: str
    status: str
    text: str
    refused: bool
    usage: TokenUsage | None
    unsupported_output: bool = False


@dataclass(frozen=True)
class TransportFailure:
    code: FailureCode
    charge_uncertain: bool


class ResponsesTransport(Protocol):
    def send(
        self, prompt: str, options: ProviderOptions
    ) -> TransportResponse | TransportFailure: ...


class OpenAIResponsesTransport:
    """Construction is inert; importing the SDK and reading the key happen in send."""

    def send(self, prompt: str, options: ProviderOptions) -> TransportResponse | TransportFailure:
        if options.mode is not ProviderMode.OPENAI_LIVE:
            return TransportFailure(FailureCode.DISABLED, False)
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
                response = client.responses.create(
                    model=options.model,
                    input=prompt,
                    store=False,
                    stream=False,
                    tools=[],
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
            for item in response.output:
                if item.type == "message":
                    for content in item.content:
                        if content.type == "refusal":
                            refused = True
                        elif content.type == "output_text":
                            texts.append(content.text)
                elif item.type != "reasoning":
                    unsupported = True
            usage = response.usage
            return TransportResponse(
                model=response.model,
                status=response.status or "missing",
                text="".join(texts),
                refused=refused,
                usage=(
                    None
                    if usage is None
                    else TokenUsage(usage.input_tokens, usage.output_tokens, usage.total_tokens)
                ),
                unsupported_output=unsupported or len(texts) != 1,
            )
        except APITimeoutError:
            return TransportFailure(FailureCode.TIMEOUT, True)
        except Exception:
            # No SDK exception text, request, response or credential enters diagnostics.
            return TransportFailure(FailureCode.TRANSPORT_FAILURE, True)
