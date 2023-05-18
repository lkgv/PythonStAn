from typing import Dict, Type, Any

from pythonstan.ir.ir_module import IRModule
from pythonstan.world import World
from ..analysis import Analysis, AnalysisConfig, AnalysisDriver
from .transform import Transform


class TransformDriver(AnalysisDriver):
    transform: Type[Transform]
    results: Any

    def __init__(self, config):
        self.config = config
        self.transform = Transform.get_analysis(config.id)

    def analyze(self, module: IRModule) -> Any:
        analyzer = self.transform(self.config)
        for prev in self.config.prev_analysis:
            prev_results = World().analysis_manager.get_results(prev)
            if prev_results is not None:
                analyzer.set_input(prev, prev_results[module.get_name()])
        analyzer.transform(module)
        return analyzer.results
