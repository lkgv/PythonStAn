"""Immutable effective configuration consumed by source and session execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, override

from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_json_object,
    require_array,
    require_bool,
    require_int,
    require_number,
    require_object,
    require_string,
    thaw_json,
    thaw_json_object,
    validate_json_object,
)
from pythonstan.analysis.contracts.wire import thaw_snapshot

from .errors import ConfigurationError
from .llm_provider import ProviderOptions

if TYPE_CHECKING:
    from pythonstan.results.output import OutputSpec
    from pythonstan.results.queries import QuerySpec

type EngineName = Literal["native", "native_then_ai", "ai_primary", "hybrid"]


class ConfigTarget(Protocol):
    """Only source-location metadata is needed for configuration discovery."""

    @property
    def kind(self) -> str: ...

    @property
    def config_search_root(self) -> Path | None: ...


def string_tuple(value: JsonValue) -> tuple[str, ...]:
    return tuple(require_string(item) for item in require_array(value))


def engine_name(value: JsonValue) -> EngineName:
    name = require_string(value)
    match name:
        case "native" | "native_then_ai" | "ai_primary" | "hybrid":
            return name
        case _:
            raise ConfigurationError(f"Unsupported engine: {name}")


@dataclass(frozen=True)
class InputsConfig:
    project_root: str | None
    source_roots: tuple[str, ...]
    exclusions: tuple[str, ...]
    entries: tuple[str, ...]
    bindings: FrozenJsonObject

    @classmethod
    def from_wire(cls, value: object) -> InputsConfig:
        """Decode source options from an already-resolved configuration document."""
        try:
            data = validate_json_object(value)
            unknown = set(data) - {
                "project_root",
                "source_roots",
                "exclusions",
                "entries",
                "bindings",
            }
            if unknown:
                raise ConfigurationError(f"Unknown input configuration fields: {sorted(unknown)}")
            root = data.get("project_root")
            return cls(
                None if root is None else require_string(root),
                string_tuple(data.get("source_roots", [])),
                string_tuple(data.get("exclusions", [])),
                string_tuple(data.get("entries", [])),
                freeze_json_object(require_object(data.get("bindings", {}))),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ConfigurationError):
                raise
            raise ConfigurationError(str(error)) from error


@dataclass(frozen=True)
class ResourceConfig:
    max_work: int
    max_seconds: float
    max_memory_mb: int
    max_reanalyses: int

    @classmethod
    def from_wire(cls, data: JsonObject) -> ResourceConfig:
        return cls(
            require_int(data["max_work"]),
            require_number(data["max_seconds"]),
            require_int(data["max_memory_mb"]),
            require_int(data["max_reanalyses"]),
        )


@dataclass(frozen=True)
class ObservationConfig:
    enabled: bool
    max_events: int

    @classmethod
    def from_wire(cls, data: JsonObject) -> ObservationConfig:
        return cls(require_bool(data["enabled"]), require_int(data["max_events"]))


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool
    level1: bool
    level2: bool
    provider: str
    proposals: tuple[FrozenJsonValue, ...]
    max_proposals: int
    checker_budget: int
    provider_options: ProviderOptions

    @classmethod
    def from_wire(cls, data: JsonObject) -> LlmConfig:
        return cls(
            require_bool(data["enabled"]),
            require_bool(data["level1"]),
            require_bool(data["level2"]),
            require_string(data["provider"]),
            tuple(freeze_json(item) for item in require_array(data["proposals"])),
            require_int(data["max_proposals"]),
            require_int(data["checker_budget"]),
            ProviderOptions.from_wire(data),
        )


@dataclass(frozen=True, init=False)
class EffectiveConfig(Mapping[str, JsonValue]):
    """One checked request configuration; mutable projections are detached copies."""

    _document: FrozenJsonObject
    _provenance: Mapping[str, str]
    config_file: str | None
    engine: EngineName
    semantic_profile: str
    inputs: InputsConfig
    resources: ResourceConfig
    observation: ObservationConfig
    llm: LlmConfig
    regions: tuple[FrozenJsonValue, ...]
    queries: tuple[QuerySpec, ...]
    output: OutputSpec
    andersen: FrozenJsonObject
    abstract: FrozenJsonObject

    def __init__(
        self,
        document: JsonObject,
        provenance: Mapping[str, str],
        *,
        queries: tuple[QuerySpec, ...],
        output: OutputSpec,
        config_file: str | None = None,
    ) -> None:
        data = validate_json_object(document)
        data["queries"] = [query.to_dict() for query in queries]
        data["output"] = output.to_dict()
        object.__setattr__(self, "_document", freeze_json_object(data))
        owned_provenance: Mapping[str, str] = MappingProxyType(dict[str, str](provenance))
        object.__setattr__(self, "_provenance", owned_provenance)
        object.__setattr__(self, "config_file", config_file)
        object.__setattr__(self, "engine", engine_name(data["engine"]))
        object.__setattr__(self, "semantic_profile", require_string(data["semantic_profile"]))
        object.__setattr__(self, "inputs", InputsConfig.from_wire(data["inputs"]))
        object.__setattr__(
            self, "resources", ResourceConfig.from_wire(require_object(data["resources"]))
        )
        object.__setattr__(
            self, "observation", ObservationConfig.from_wire(require_object(data["observation"]))
        )
        object.__setattr__(self, "llm", LlmConfig.from_wire(require_object(data["llm"])))
        owned_regions: tuple[FrozenJsonValue, ...] = tuple(
            freeze_json(item) for item in require_array(data["regions"])
        )
        object.__setattr__(self, "regions", owned_regions)
        object.__setattr__(self, "queries", queries)
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "andersen", freeze_json_object(require_object(data["andersen"])))
        object.__setattr__(self, "abstract", freeze_json_object(require_object(data["abstract"])))

    def to_dict(self, *, visit: Callable[[int], None] | None = None) -> JsonObject:
        if visit is None:
            return thaw_json_object(self._document)
        return require_object(thaw_snapshot(self._document, visit=visit))

    @property
    def provenance(self) -> dict[str, str]:
        return dict(self._provenance)

    @property
    def digest(self) -> str:
        return self.digest_for()

    def digest_for(self, *, visit: Callable[[int], None] | None = None) -> str:
        payload = json.dumps(
            self.to_dict(visit=visit),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @override
    def __getitem__(self, key: str) -> JsonValue:
        return thaw_json(self._document[key])

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._document)

    @override
    def __len__(self) -> int:
        return len(self._document)


AnalysisConfig = EffectiveConfig
ResolvedConfig = EffectiveConfig


type ConfigInput = EffectiveConfig | Mapping[str, object] | str | PathLike[str] | None
