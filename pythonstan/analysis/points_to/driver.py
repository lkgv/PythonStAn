from ..analysis import AnalysisDriver, AnalysisConfig

__all__ = ['PointsToDriver']

class PointsToDriver:
    def __init__(self, config: AnalysisConfig):
        super().__init__(config)

    def analyze(self):
        from pythonstan.world import World
        entry_mod = World().entry_module
        self.results = ...