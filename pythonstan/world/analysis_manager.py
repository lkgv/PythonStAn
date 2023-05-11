from typing import Dict, List, Any, Tuple, Literal
from queue import Queue

from pythonstan.analysis import Analysis, AnalysisConfig
from pythonstan.ir import IRModule

class AnalysisManager:
    prev_analysis: Dict[AnalysisConfig, List[AnalysisConfig]]
    next_analysis: Dict[AnalysisConfig, List[AnalysisConfig]]
    analysis_configs: List[AnalysisConfig]
    results: Dict[Analysis, Any]
    ir: Dict[Tuple[IRModule, str], IRModule]

    def reset(self, configs):
        ...

    def analysis(self, analyzer, module):
        prev_analysis = analyzer.config.prev_analysis
        prev_results = {}
        for anal in prev_analysis:
            prev_results[anal.id] = self.results[anal]
        ir = self.get_ir(module, analyzer.config.ir_type)
        ...
        self.results[analyzer] = ...

    def generator(self):
        visited = {*()}
        queue = Queue()
        for anal_config in self.analysis_configs:
            if len(self.prev_analysis[anal_config]) == 0:
                queue.put(anal_config)
        while not queue.empty():
            cur_config = queue.get()
            visited.add(cur_config)
            analyzer = make_analyzer(cur_config)
            ...

            yield analyzer

            for succ in self.next_analysis[cur_config]:
                if succ not in visited:
                    queue.put(succ)

    def transform(self, transformer, module):
        ...

    def get_ir(self, module: IRModule,
               ir_type: Literal['three_address','ssa']) -> IRModule:
        return self.ir[(module, ir_type)]

    def get_results(self, analysis: Analysis):
        return self.results[analysis]
