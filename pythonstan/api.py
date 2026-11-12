"""Public API; every input is resolved once before session construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .config import AnalysisConfig as AnalysisConfig
from .config import ConfigInput
from .config import ConfigurationError as ConfigurationError
from .inputs import File as File
from .inputs import Project as Project
from .inputs import Source as Source
from .inputs.targets import Target
from .request import ResolvedRequest as ResolvedRequest
from .request import resolve_request as resolve_request
from .results import AnalysisResult as AnalysisResult
from .results import OutputSpec as OutputSpec
from .results import PropertyQuery as PropertyQuery
from .results import QueryAnswer as QueryAnswer
from .results import ValueQuery as ValueQuery
from .results.queries import QueryInput
from .session import AnalysisSession as AnalysisSession


def analyze(
    target: Target,
    *,
    config: ConfigInput = None,
    overrides: Mapping[str, object] | None = None,
    queries: Sequence[QueryInput] | None = None,
    output: OutputSpec | Mapping[str, object] | None = None,
) -> AnalysisResult:
    request = resolve_request(
        target, config=config, overrides=overrides, queries=queries, output=output
    )
    with AnalysisSession(request) as session:
        return session.execute()


__all__ = [
    "analyze",
    "Source",
    "File",
    "Project",
    "OutputSpec",
    "ValueQuery",
    "PropertyQuery",
    "AnalysisResult",
    "QueryAnswer",
    "AnalysisConfig",
    "ConfigurationError",
    "AnalysisSession",
    "ResolvedRequest",
    "resolve_request",
]
