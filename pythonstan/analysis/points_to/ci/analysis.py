import copy
from typing import Dict, Any
from abc import abstractmethod

from pythonstan.ir import IRScope
from .node_transfer import NodeTransfer
from .edge_transfer import EdgeTransfer
from ...analysis import Analysis, AnalysisConfig


class CIPTSAnalysis(Analysis):
    module: IRScope
    inputs: Dict[str, Any]
    results: Any

    @abstractmethod
    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.inputs = {}

    def set_input(self, key, value):
        self.inputs[key] = value

    def get_input(self, key):
        return self.inputs[key]

    def analyze(self, module: IRScope):
        ...

    def make_analysis_lattice(self, graph):
        pass

    def get_node_transfer_functions(self) -> NodeTransfer:
        ...

    def get_edge_transfer_functions(self) -> EdgeTransfer:
        ...
