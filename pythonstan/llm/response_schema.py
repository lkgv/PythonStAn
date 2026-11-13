"""Strict wire schema for untrusted finite dispatch candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.analysis.verification.guarded_dispatch.language import PROFILE

if TYPE_CHECKING:
    from pythonstan.config.llm_provider import ProviderOptions


def _record(properties: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: JsonObject) -> JsonObject:
    return {"type": "array", "items": items, "maxItems": 4096}


def candidate_schema() -> JsonObject:
    """Build fresh owned JSON; the server schema never grants local trust."""
    text: JsonObject = {"type": "string", "maxLength": 4096}
    boolean: JsonObject = {"type": "boolean"}
    relation: JsonObject = {"type": "string", "enum": ["input", "fresh_box", "top"]}
    dependency = _record({"kind": text, "identity": text, "version": text, "negative": boolean})
    identity = _record(
        {
            **dict.fromkeys(
                (
                    "source_digest",
                    "ir_digest",
                    "model_digest",
                    "policy_digest",
                    "profile",
                    "state_version",
                    "python_version",
                ),
                text,
            ),
            "dependencies": _array(dependency),
        }
    )
    guard = _record(
        {
            "table_name": text,
            "keys": _array(text),
            "exact_builtin_dict": {"type": "boolean", "enum": [True]},
            "stable_bindings": {"type": "boolean", "enum": [True]},
            "payload": {"type": "string", "enum": ["all_modeled_values"]},
        }
    )
    handler = _record(
        {
            "name": text,
            "relation": relation,
            "input_parameter": text,
            "slot": {"type": ["string", "null"], "maxLength": 4096},
            "existing_heap": {"type": "string", "enum": ["unchanged"]},
            "normal_exit": boolean,
            "exceptions": _array(text),
            "suspends": boolean,
        }
    )
    fields: dict[str, JsonValue] = {
        "schema_version": {"type": "integer", "enum": [1]},
        "profile": {"type": "string", "enum": [PROFILE]},
        "identity": identity,
        "dispatch": text,
        "guard": guard,
        "handlers": _array(handler),
        "arms": _array(_record({"key": text, "handler": text, "relation": relation})),
    }
    return _record(fields)


def response_request(prompt: str, options: ProviderOptions) -> JsonObject:
    """The exact body whose digest and quotation authorize one SDK request."""
    return {
        "model": options.model,
        "input": prompt,
        "store": False,
        "stream": False,
        "tools": [],
        "prompt_cache_options": {"mode": "explicit"},
        "max_output_tokens": options.max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "pythonstan_dispatch_candidate",
                "strict": True,
                "schema": candidate_schema(),
            }
        },
    }
