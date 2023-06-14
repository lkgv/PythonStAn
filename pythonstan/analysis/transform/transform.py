import copy
from typing import Dict, Any
from abc import abstractmethod

from pythonstan.ir.ir_module import IRModule
from ..analysis import Analysis, AnalysisConfig, AnalysisDriver


class Transform(Analysis):
    module: IRModule
    inputs: Dict[str, Any]
    results: Any

    @abstractmethod
    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.inputs = {}

    def set_input(self, key, value):
        self.inputs[key] = value

    @abstractmethod
    def transform(self, module: IRModule):
        ...
