from typing import Dict, List, Any, Tuple, Literal
from queue import Queue

from pythonstan.analysis import AnalysisDriver, AnalysisConfig
from pythonstan.ir import IRModule

DEFAULT_ANALYSIS = [
    AnalysisConfig(
        name="three address",
        id="ThreeAddress"
    ),
    AnalysisConfig(
        name="block cfg",
        id="BlockCFG",
        prev_analysis=["three address"]
    ),
    AnalysisConfig(
        name="cfg",
        id="CFG",
        prev_analysis=["block cfg"]
    ),
    AnalysisConfig(
        name="ssa",
        id="SSA",
        prev_analysis=["cfg"]
    )
]


class AnalysisManager:
    prev_analyzers: Dict[str, List[AnalysisDriver]]
    next_analyzers: Dict[str, List[AnalysisDriver]]
    analysis_configs: List[AnalysisConfig]
    analyzers: Dict[str, AnalysisDriver]
    results: Dict[str, Any]

    def reset(self):
        self.prev_analyzers = {}
        self.next_analyzers = {}
        self.analysis_configs = []
        self.analyzers = {}
        self.results = {}
        self.load_default_analysis()

    def build(self, configs: List[AnalysisConfig]):
        self.reset()
        for config in DEFAULT_ANALYSIS:
            self.add_analyzer(config)
        for config in configs:
            self.add_analyzer(config)

    def add_analyzer(self, config: AnalysisConfig):
        analyzer = analasisDriver

    def analysis(self, analyzer_name: str, module):
        analyzer = self.analyzers.get(analyzer_name, None)
        if analyzer is None:
            raise NotImplementedError(f"Analysis {analyzer_name} not implemented!")
        self.do_analysis(analyzer, module)

    def do_analysis(self, analyzer, module):
        prev_analysis = analyzer.config.prev_analysis
        prev_results = {}
        for anal in prev_analysis:
            prev_results[anal.name] = self.results[anal.name]
        # get ir is in the implement of analysis like World().scope_manager.get_ir(...)
        ...
        self.results[analyzer] = ...

    def generator(self):
        visited = {*()}
        queue = Queue()
        for name, analyzer in self.analyzers:
            if len(self.prev_analyzers[name]) == 0:
                queue.put(analyzer)
        while not queue.empty():
            cur_analyzer = queue.get()
            cur_name = cur_analyzer.name
            visited.add(cur_name)
            ...

            yield cur_analyzer

            for succ in self.next_analyzers[cur_name]:
                if succ.name not in visited:
                    queue.put(succ)

    def transform(self, transformer, module):
        ...

    def get_analyzer(self, name):
        return self.analyzers[name]

    def get_results(self, analysis: Analysis):
        return self.results[analysis.config.name]

    def load_default_analysis(self):
        three_address_config = AnalysisConfig()
        cfg_gen_config = AnalysisConfig()
        ssa_gen_config = AnalysisConfig()
        # Here need to add transformers
