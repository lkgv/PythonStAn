from typing import Dict, List, Any, Tuple, Literal
from queue import Queue

from pythonstan.analysis import Analysis, AnalysisDriver, AnalysisConfig
from pythonstan.analysis.transform import TransformDriver
from pythonstan.analysis.dataflow import DataflowAnalysisDriver
from pythonstan.analysis.mini_cg import MiniCGAnalysisDriver
from pythonstan.ir import IRModule

DEFAULT_ANALYSIS = [
    AnalysisConfig(
        name="three address",
        id="ThreeAddress",
        options={"type": "transform"}
    ),
    AnalysisConfig(
        name="ir",
        id="IR",
        options={"type": "transform"}
    ),
    AnalysisConfig(
        name="block cfg",
        id="BlockCFG",
        prev_analysis=["three address"],
        options={"type": "transform"}
    ),
    AnalysisConfig(
        name="cfg",
        id="CFG",
        prev_analysis=["block cfg"],
        options={"type": "transform"}
    ),
    AnalysisConfig(
        name="ssa",
        id="SSA",
        prev_analysis=["cfg"],
        options={"type": "transform"}
    )
]


class AnalysisManager:
    prev_analyzers: Dict[str, List[str]]
    next_analyzers: Dict[str, List[str]]
    analysis_configs: List[AnalysisConfig]
    analyzers: Dict[str, AnalysisDriver]
    results: Dict[str, Any]

    def reset(self):
        self.prev_analyzers = {}
        self.next_analyzers = {}
        self.analysis_configs = []
        self.analyzers = {}
        self.results = {}

    def build(self, configs: List[AnalysisConfig]):
        self.reset()
        for config in DEFAULT_ANALYSIS:
            self.add_analyzer(config)
        for config in configs:
            self.add_analyzer(config)

    def add_analyzer(self, config: AnalysisConfig):
        name = config.name
        analyzer = self.gen_analyzer(config)
        self.analysis_configs.append(config)
        self.analyzers[name] = analyzer
        self.prev_analyzers[name] = []
        self.next_analyzers[name] = []
        for prev_name in config.prev_analysis:
            self.prev_analyzers[name].append(prev_name)
            self.next_analyzers[prev_name].append(name)

    def gen_analyzer(self, config: AnalysisConfig) -> AnalysisDriver:
        if config.type == "transform":
            analyzer = TransformDriver(config)
        elif config.type == "dataflow analysis":
            analyzer = DataflowAnalysisDriver(config)
        elif config.type == 'inter-procedure':
            analyzer = MiniCGAnalysisDriver(config)
        else:
            raise NotImplementedError
        return analyzer

    def analysis(self, analyzer_name: str, module: IRModule):
        analyzer = self.analyzers.get(analyzer_name, None)
        if analyzer is None:
            raise NotImplementedError(f"Analysis {analyzer_name} not implemented!")
        self.do_analysis(analyzer, module)

    def do_analysis(self, analyzer: AnalysisDriver, module: IRModule):
        prev_results = {}
        for anal_name in self.prev_analyzers[analyzer.config.name]:
            prev_results[anal_name] = self.results[anal_name]
        # print(analyzer.config.id, analyzer.config.name)
        analyzer.analyze(module, prev_results)
        self.results[analyzer.config.name] = analyzer.results

    def generator(self):
        visited = {*()}
        queue = Queue()
        for name, analyzer in self.analyzers.items():
            if len(self.prev_analyzers[name]) == 0:
                queue.put(name)
        while not queue.empty():
            cur_name = queue.get()
            visited.add(cur_name)
            cur_analyzer = self.analyzers[cur_name]
            if cur_analyzer.config not in DEFAULT_ANALYSIS:
                yield cur_analyzer
            for succ in self.next_analyzers[cur_name]:
                if succ not in visited:
                    queue.put(succ)

    def get_analyzer(self, name):
        return self.analyzers[name]

    def get_results(self, name: str):
        return self.results[name]
