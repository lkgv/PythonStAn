from pythonstan.graph.cfg.builder import CFGBuilder
from pythonstan.ir import IRModule
from pythonstan.analysis import AnalysisConfig
from .namespace import Namespace
from .world import World

from typing import List
from queue import Queue

class PipelineConfig:
    filename: str
    project_path: str
    analysis_list: List[AnalysisConfig]


class Pipeline:
    config: PipelineConfig

    def __init__(self, config: PipelineConfig):
        World().reset()
        self.config = config

    def analyse_intra_procedure(self, analyzer, module_graph):
        q = Queue()
        for entry in module_graph.get_entries():
            q.put(entry)
        visited = {*()}
        while not q.empty():
            cur_module = q.get()
            result = analyzer.analysis(cur_module)
            # ... add result
            visited.add(cur_module)
            for succ in module_graph.succs_of(cur_module):
                if not succ in visited:
                    q.put(succ)

    def analyse_inter_procedure(self, analyzer, module_graph):
        ...

    def do_analysis(self, analyzer_generator, module_graph):
        for analyzer in analyzer_generator:
            if analyzer.config.inter_procedural:
                self.analyse_inter_procedure(analyzer, module_graph)
            else:
                self.analyse_intra_procedure(analyzer, module_graph)

    def run(self):
        filename = self.config.filename
        World().analysis_manager.build_analysis(self.config.analysis_list)
        World().load_module(self.config, filename)
        module_graph = World().import_manager.gen_graph()
        analyzer_generator = World().analysis_manager.generator()
        self.do_analysis(module_graph, analyzer_generator)
