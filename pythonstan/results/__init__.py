"""Public result and query records backed by one canonical implementation."""

from .output import OutputSpec as OutputSpec
from .queries import (
    PropertyQuery as PropertyQuery,
)
from .queries import (
    QuerySpec as QuerySpec,
)
from .queries import (
    ValueQuery as ValueQuery,
)
from .queries import (
    ViewQuery as ViewQuery,
)
from .queries import (
    normalize_query as normalize_query,
)
from .result import AnalysisResult as AnalysisResult
from .result import QueryAnswer as QueryAnswer

__all__ = [
    "AnalysisResult",
    "QueryAnswer",
    "OutputSpec",
    "ValueQuery",
    "PropertyQuery",
    "ViewQuery",
    "QuerySpec",
    "normalize_query",
]
