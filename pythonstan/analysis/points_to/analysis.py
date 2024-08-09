from ..analysis import AnalysisConfig
from .heap_model import HeapModel

class PointsToAnalysis:
    def __init__(self, config: AnalysisConfig):
        self.config = config

    def analyze(self):
        options = self.config.options
        heap_model = HeapModel()

    def run_analysis(self, heap_model, selector):
        ...

    def set_plugin(self, solver, options):
        ...

    def add_plugin(self, plugin):
        ...
