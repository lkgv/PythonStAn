import time
from collections.abc import Generator

from pythonstan.analysis.analysis import AnalysisConfig, AnalysisDriver, order_analysis_configs
from pythonstan.analysis.closure import ClosureAnalysis
from pythonstan.analysis.dataflow.driver import DataflowAnalysisDriver
from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis as PointerAnalysisDriver

# Analysis drivers
from pythonstan.analysis.transform.driver import TransformDriver
from pythonstan.compat.legacy_ai.analysis import AIAnalysisDriver
from pythonstan.ir import IRModule

DEFAULT_ANALYSIS = [
    AnalysisConfig(name="three address", id="ThreeAddress", options={"type": "transform"}),
    AnalysisConfig(
        name="ir", id="IR", prev_analysis=["three address"], options={"type": "transform"}
    ),
    AnalysisConfig(
        name="block cfg", id="BlockCFG", prev_analysis=["ir"], options={"type": "transform"}
    ),
    AnalysisConfig(
        name="cfg", id="CFG", prev_analysis=["block cfg"], options={"type": "transform"}
    ),
    AnalysisConfig(
        name="closure", id="Closure", prev_analysis=["cfg"], options={"type": "closure analysis"}
    ),
]


class AnalysisManager:
    prev_analyzers: dict[str, list[str]]
    next_analyzers: dict[str, list[str]]
    analysis_configs: list[AnalysisConfig]
    analyzers: dict[str, AnalysisDriver]
    results: dict[str, object]
    time_count: bool

    def __init__(self) -> None:
        self.time_count = False
        self.reset()

    def reset(self) -> None:
        self.prev_analyzers = {}
        self.next_analyzers = {}
        self.analysis_configs = []
        self.analyzers = {}
        self.results = {}

    def build(self, configs: list[AnalysisConfig]) -> None:
        self.reset()
        for config in order_analysis_configs([*DEFAULT_ANALYSIS, *configs]):
            self.add_analyzer(config)

    def set_time_count(self, time_count: bool):
        self.time_count = time_count

    def add_analyzer(self, config: AnalysisConfig):
        name = config.name
        if name in self.analyzers:
            raise ValueError(f"Duplicate analysis name: {name}")
        analyzer = self.gen_analyzer(config)
        self.analysis_configs.append(config)
        self.analyzers[name] = analyzer
        self.prev_analyzers[name] = []
        self.next_analyzers.setdefault(name, [])
        for prev_name in config.prev_analysis:
            self.prev_analyzers[name].append(prev_name)
            self.next_analyzers.setdefault(prev_name, []).append(name)

    def gen_analyzer(self, config: AnalysisConfig) -> AnalysisDriver:
        analyzer: AnalysisDriver
        if config.type == "transform":
            analyzer = TransformDriver(config)
        elif config.type == "dataflow analysis":
            analyzer = DataflowAnalysisDriver(config)
        elif config.type == "pointer analysis":
            analyzer = PointerAnalysisDriver(config)
        elif config.type == "ai analysis":
            analyzer = AIAnalysisDriver(config)
        elif config.type == "certified analysis":
            from pythonstan.analysis.pointer.ai.certified.driver import SourceCertificateDriver

            analyzer = SourceCertificateDriver(config)
        elif config.type == "closure analysis":
            analyzer = ClosureAnalysis(config)
        else:
            raise NotImplementedError(f"Unknown analysis type: {config.type}")
        return analyzer

    def analysis(self, analyzer_name: str, module: IRModule):
        if self.time_count:
            start_time = time.perf_counter()

        analyzer = self.analyzers.get(analyzer_name, None)
        if analyzer is None:
            raise NotImplementedError(f"Analysis {analyzer_name} not implemented!")
        self.do_analysis(analyzer, module)

        if self.time_count:
            end_time = time.perf_counter()
            if not isinstance(analyzer, TransformDriver):
                print(
                    f"Analysis {analyzer_name} for module {module.get_qualname()} took {end_time - start_time:.2f} seconds"
                )

    def do_analysis(self, analyzer: AnalysisDriver, module: IRModule):
        prev_results = {}
        for anal_name in self.prev_analyzers[analyzer.config.name]:
            prev_results[anal_name] = self.results[anal_name]
        analyzer.analyze(module, prev_results)
        self.results[analyzer.config.name] = analyzer.results

    def generator(self) -> Generator[AnalysisDriver, None, None]:
        # Materialize the whole order before yielding, so an invalid graph
        # cannot run a prefix of analysis passes before reporting the error.
        ordered = order_analysis_configs(self.analysis_configs)
        for config in ordered:
            if config not in DEFAULT_ANALYSIS:
                yield self.analyzers[config.name]

    def get_analyzer(self, name: str) -> AnalysisDriver:
        return self.analyzers[name]

    def get_results(self, name: str) -> object:
        return self.results[name]
