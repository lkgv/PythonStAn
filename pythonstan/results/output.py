"""Immutable output selection with one checked external Mapping boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_int,
    require_string,
    validate_json_object,
)
from pythonstan.config.errors import ConfigurationError

from .queries import VIEWS, ViewKind, parse_view

type OutputFormat = Literal["json", "text"]
_SEQUENCE_FIELDS = ("views", "modules", "functions", "regions", "contexts", "fields")
_OUTPUT_FIELDS = frozenset((*_SEQUENCE_FIELDS, "max_items", "format"))


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ConfigurationError(f"{name} must be a sequence of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigurationError(f"{name} must contain strings")
        result.append(item)
    return tuple(result)


def _format(value: object) -> OutputFormat:
    if value == "json":
        return "json"
    if value == "text":
        return "text"
    raise ConfigurationError("format must be json or text")


@dataclass(frozen=True, init=False)
class OutputSpec:
    views: tuple[ViewKind, ...]
    modules: tuple[str, ...]
    functions: tuple[str, ...]
    regions: tuple[str, ...]
    contexts: tuple[str, ...]
    fields: tuple[str, ...]
    max_items: int
    format: OutputFormat

    def __init__(
        self,
        views: Sequence[str] = ("summary",),
        modules: Sequence[str] = (),
        functions: Sequence[str] = (),
        regions: Sequence[str] = (),
        contexts: Sequence[str] = (),
        fields: Sequence[str] = (),
        max_items: int = 10000,
        format: str = "json",
    ) -> None:
        validated_views = _strings(views, "views")
        unknown_views = set(validated_views) - set(VIEWS)
        if unknown_views:
            raise ConfigurationError(f"Unknown output views: {sorted(unknown_views)}")
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 0:
            raise ConfigurationError("max_items must be nonnegative")
        object.__setattr__(self, "views", tuple(parse_view(view) for view in validated_views))
        object.__setattr__(self, "modules", _strings(modules, "modules"))
        object.__setattr__(self, "functions", _strings(functions, "functions"))
        object.__setattr__(self, "regions", _strings(regions, "regions"))
        object.__setattr__(self, "contexts", _strings(contexts, "contexts"))
        object.__setattr__(self, "fields", _strings(fields, "fields"))
        object.__setattr__(self, "max_items", max_items)
        object.__setattr__(self, "format", _format(format))

    def to_dict(self) -> JsonObject:
        return {
            "views": list(self.views),
            "modules": list(self.modules),
            "functions": list(self.functions),
            "regions": list(self.regions),
            "contexts": list(self.contexts),
            "fields": list(self.fields),
            "max_items": self.max_items,
            "format": self.format,
        }


def parse_output(raw: object, *, defaults: OutputSpec | None = None) -> OutputSpec:
    if raw is None:
        return defaults if defaults is not None else OutputSpec()
    if isinstance(raw, OutputSpec):
        return raw
    if not isinstance(raw, Mapping):
        raise ConfigurationError("output must be OutputSpec or a Mapping")
    try:
        data = validate_json_object(raw)
        unknown = data.keys() - _OUTPUT_FIELDS
        if unknown:
            raise ConfigurationError(f"Unknown output fields: {sorted(unknown)}")
        base = (defaults if defaults is not None else OutputSpec()).to_dict()
        base.update(data)
        return OutputSpec(
            views=_strings(base["views"], "views"),
            modules=_strings(base["modules"], "modules"),
            functions=_strings(base["functions"], "functions"),
            regions=_strings(base["regions"], "regions"),
            contexts=_strings(base["contexts"], "contexts"),
            fields=_strings(base["fields"], "fields"),
            max_items=require_int(base["max_items"]),
            format=require_string(base["format"]),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError(str(error)) from error
