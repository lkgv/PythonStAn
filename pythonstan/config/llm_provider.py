"""Non-secret configuration for the optional candidate transport."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_bool,
    require_int,
    require_number,
    require_string,
)

from .errors import ConfigurationError


class ProviderMode(StrEnum):
    DISABLED = "disabled"
    OFFLINE = "offline"
    REPLAY = "replay"
    ADVERSARIAL = "adversarial"
    OPENAI_LIVE = "openai_live"


@dataclass(frozen=True)
class ProviderOptions:
    mode: ProviderMode
    model: str = "gpt-6-luna"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2048
    max_input_bytes: int = 1_000_000
    max_response_bytes: int = 131072
    max_json_depth: int = 24
    max_calls: int = 1
    max_total_tokens: int = 8192

    def __post_init__(self) -> None:
        if self.model != "gpt-6-luna":
            raise ConfigurationError("Only the registered gpt-6-luna provider model is supported")
        if not 0 < self.timeout_seconds <= 300:
            raise ConfigurationError("LLM timeout_seconds must be in (0, 300]")
        bounds = (
            ("max_output_tokens", self.max_output_tokens, 32768),
            ("max_input_bytes", self.max_input_bytes, 1_000_000),
            ("max_response_bytes", self.max_response_bytes, 1_000_000),
            ("max_json_depth", self.max_json_depth, 64),
            ("max_calls", self.max_calls, 32),
            ("max_total_tokens", self.max_total_tokens, 1_000_000),
        )
        for name, value, upper in bounds:
            if type(value) is not int or not 1 <= value <= upper:
                raise ConfigurationError(f"LLM {name} must be an integer in 1..{upper}")
        if self.max_output_tokens > self.max_total_tokens:
            raise ConfigurationError("LLM max_output_tokens exceeds max_total_tokens")

    @classmethod
    def from_wire(cls, data: JsonObject) -> ProviderOptions:
        if set(data) - {
            "enabled",
            "level1",
            "level2",
            "provider",
            "proposals",
            "max_proposals",
            "checker_budget",
            "model",
            "timeout_seconds",
            "max_output_tokens",
            "max_input_bytes",
            "max_response_bytes",
            "max_json_depth",
            "max_calls",
            "max_total_tokens",
        }:
            raise ConfigurationError("Unknown LLM provider configuration fields")
        name = require_string(data.get("provider", "offline"))
        if name == "recorded":
            name = "replay"
        try:
            mode = ProviderMode(name)
        except ValueError as error:
            raise ConfigurationError("Unsupported LLM provider") from error
        if not require_bool(data.get("enabled", False)):
            mode = ProviderMode.DISABLED
        options = cls(
            mode=mode,
            model=require_string(data.get("model", "gpt-6-luna")),
            timeout_seconds=require_number(data.get("timeout_seconds", 30.0)),
            max_output_tokens=require_int(data.get("max_output_tokens", 2048)),
            max_input_bytes=require_int(data.get("max_input_bytes", 1_000_000)),
            max_response_bytes=require_int(data.get("max_response_bytes", 131072)),
            max_json_depth=require_int(data.get("max_json_depth", 24)),
            max_calls=require_int(data.get("max_calls", 1)),
            max_total_tokens=require_int(data.get("max_total_tokens", 8192)),
        )
        if options.mode is ProviderMode.OPENAI_LIVE and (
            require_bool(data.get("level1", False)) or not require_bool(data.get("level2", False))
        ):
            raise ConfigurationError("openai_live supports L2 candidate proposals only")
        if require_bool(data.get("enabled", False)) and mode is ProviderMode.DISABLED:
            raise ConfigurationError("Enabled LLM cannot select the disabled provider")
        return options
