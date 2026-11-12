"""The sole public-input normalization boundary for an analysis run."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .config import ConfigInput, ResolvedConfig, resolve_config
from .config.errors import ConfigurationError
from .inputs import File, InputError, Project, Source
from .inputs.targets import Target
from .results.output import OutputSpec
from .results.queries import QueryInput, QuerySpec


@dataclass(frozen=True)
class ResolvedRequest:
    target: Target
    config: ResolvedConfig
    started_at: float
    resolution_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, (Source, File, Project)):
            raise InputError("target must be Source, File, or Project")
        if not isinstance(self.config, ResolvedConfig):
            raise ConfigurationError("ResolvedRequest requires a resolved configuration")

    @property
    def queries(self) -> tuple[QuerySpec, ...]:
        return self.config.queries

    @property
    def output(self) -> OutputSpec:
        return self.config.output


def resolve_request(
    target: Target,
    *,
    config: ConfigInput = None,
    overrides: Mapping[str, object] | None = None,
    queries: Sequence[QueryInput] | None = None,
    output: OutputSpec | Mapping[str, object] | None = None,
) -> ResolvedRequest:
    started_at = time.monotonic()
    if not isinstance(target, (Source, File, Project)):
        raise InputError("target must be Source, File, or Project")
    effective = resolve_config(target, config, overrides, queries=queries, output=output)
    resolution_seconds = time.monotonic() - started_at
    return ResolvedRequest(target, effective, started_at, resolution_seconds)
