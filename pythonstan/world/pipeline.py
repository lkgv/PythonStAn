from pythonstan.graph.cfg.builder import CFGBuilder
from pythonstan.ir import IRModule
from pythonstan.analysis import AnalysisConfig, AnalysisDriver
from .namespace import Namespace
from .world import World
from .config import Config

from typing import List
from queue import Queue

class Pipeline:
    config: Config

    def __init__(self, config=None, filename=None):
        if config is not None:
            self.config = Config.from_dict(config)
        elif filename is not None:
            self.config = Config.from_file(filename)
        else:
            raise ValueError("No proper configuration for pipeline!")
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
            print(cur_module)
            World().analysis_manager.do_analysis(analyzer, cur_module)
            visited.add(cur_module)
            for succ in module_graph.succs_of(cur_module):
                if not succ in visited:
                    q.put(succ)

    def analyse_inter_procedure(self, analyzer: AnalysisDriver):
        entry_mod = World().get_entry_module()
        World().analysis_manager.do_analysis(analyzer, entry_mod)


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
            if analyzer.config.type == "dataflow analysis":
                if analyzer.config.inter_procedure:
                    self.analyse_inter_procedure(analyzer)
                else:
                    self.analyse_intra_procedure(analyzer)
            if analyzer.config.type == 'transform':
                self.do_transform(analyzer)

    def run(self):
        analyzer_generator = World().analysis_manager.generator()
        self.do_analysis(analyzer_generator)
