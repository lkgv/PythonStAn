"""Compatibility exports for the single canonical result/query implementation."""

from .output import OutputSpec as OutputSpec
from .queries import (
    VIEWS as VIEWS,
)
from .queries import (
    PropertyQuery as PropertyQuery,
)
from .queries import (
    ValueQuery as ValueQuery,
)
from .queries import (
    normalize_query as normalize_query,
)
from .queries import (
    query_key as query_key,
)
from .result import AnalysisResult as AnalysisResult
from .result import QueryAnswer as QueryAnswer

__all__ = [
    "AnalysisResult",
    "QueryAnswer",
    "OutputSpec",
    "ValueQuery",
    "PropertyQuery",
    "VIEWS",
    "normalize_query",
    "query_key",
]
