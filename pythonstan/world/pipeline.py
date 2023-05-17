from pythonstan.graph.cfg.builder import CFGBuilder
from pythonstan.ir import IRModule
from pythonstan.analysis import AnalysisConfig
from .namespace import Namespace
from .world import World
from .config import Config

from typing import List
from queue import Queue

class Pipeline:
    config: Config

    def __init__(self, filename):
        self.config = Config.from_file(filename)
        World().setup()
        World().build(self.config)


    def analyse_intra_procedure(self, analyzer):
        module_graph = World().scope_manager.get_module_graph()
        q = Queue()
        for entry in module_graph.get_entries():
            q.put(entry)
        visited = {*()}
        while not q.empty():
            cur_module = q.get()
            World().analysis_manager.analysis(analyzer, cur_module)
            visited.add(cur_module)
            for succ in module_graph.succs_of(cur_module):
                if not succ in visited:
                    q.put(succ)

    def analyse_inter_procedure(self, analyzer):
        ...

    def do_transform(self, analyzer):
        module_graph = World().scope_manager.get_module_graph()
        q = Queue()
        for entry in module_graph.get_entries():
            q.put(entry)
        visited = {*()}
        while not q.empty():
            cur_module = q.get()
            World().analysis_manager.do_transform(analyzer, cur_module)
            visited.add(cur_module)
            for succ in module_graph.succs_of(cur_module):
                if not succ in visited:
                    q.put(succ)

    def do_analysis(self, analyzer_generator):
        for analyzer in analyzer_generator:
            if analyzer.type == "dataflow_analysis":
                if analyzer.config.inter_procedural:
                    self.analyse_inter_procedure(analyzer)
                else:
                    self.analyse_intra_procedure(analyzer)
            if analyzer.type == 'transform':
                self.do_transform(analyzer)

    def run(self):
        analyzer_generator = World().analysis_manager.generator()
        self.do_analysis(analyzer_generator)
