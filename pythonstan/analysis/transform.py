import copy
from abc import abstractmethod
from typing import Dict, Type

from pythonstan.ir.ir_module import IRModule
from .analysis import Analysis, AnalysisConfig, AnalysisDriver


class Transform(Analysis):
    module: IRModule

    @abstractmethod
    def __init__(self, module: IRModule, config: AnalysisConfig):
        super(Transform, self.__class__).__init__(config)
        if config.options.get('inplace', None):
            self.module = copy.deepcopy(module)
        else:
            self.module = module

    @abstractmethod
    def transform(self):
        ...

    @abstractmethod
    def get_modified_module(self) -> IRModule:
        ...


class TransformDriver(AnalysisDriver):
    transform: Type[Transform]
    results: Dict

    def __init__(self, config):
        self.config = config
        self.transform = Transform.get_analysis(config.id)
        self.results = {'modified_module': None}

    def analyze(self, module: IRModule):
        analyzer = self.transform(module, self.config)
        analyzer.transform()
        self.results['modified_module'] = analyzer.get_modified_module()
