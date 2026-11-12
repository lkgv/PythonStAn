from collections.abc import MutableMapping
from typing import override

from pythonstan.ir import IRScope

from ..analysis import AnalysisConfig, AnalysisDriver, get_scope_result
from .transform import Transform


class TransformDriver(AnalysisDriver):
    transform: type[Transform]

    @override
    def __init__(self, config: AnalysisConfig) -> None:
        self.config = config
        candidate = Transform.get_analysis(config.id)
        if not issubclass(candidate, Transform):
            raise TypeError("Transform id must refer to a registered Transform subclass")
        self.transform = candidate
        results: dict[object, object] = {}
        self.results = results

    @override
    def analyze(self, scope: IRScope, prev_results: dict[str, object]) -> None:
        analyzer = self.transform(self.config)
        for prev in self.config.prev_analysis:
            if prev in prev_results:
                analyzer.set_input(prev, get_scope_result(prev_results[prev], scope, prev))
        analyzer.transform(scope)
        if not isinstance(self.results, MutableMapping):
            raise TypeError("Transform results must be a mutable mapping")
        results: MutableMapping[object, object] = self.results
        if hasattr(analyzer, "results"):
            results[scope] = analyzer.results
        else:
            results[scope] = None
