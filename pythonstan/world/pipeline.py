from pythonstan.graph.cfg.builder import CFGBuilder
from pythonstan.ir import IRModule
from pythonstan.analysis import AnalysisConfig, AnalysisDriver
from .namespace import Namespace
from .world import World
from .config import Config
from .scope_manager import ModuleGraph
from .analysis_manager import AnalysisManager

from typing import List, Tuple
from queue import Queue

class Pipeline:
    config: Config
    analysis_manager: AnalysisManager

    def __init__(self, config=None, filename=None):
        if config is not None:
            self.config = Config.from_dict(config)
        elif filename is not None:
            self.config = Config.from_file(filename)
        else:
            raise ValueError("No proper configuration for pipeline!")
        World().setup()
        World().build(self.config)
        self.analysis_manager = AnalysisManager()
        self.analysis_manager.build(self.config.get_analysis_list())
        self.build_scope_graph(self.config.filename)

    def build_scope_graph(self, entry_path: str):
        entry_ns = Namespace.build(["__main__"])
        entry_mod = World().scope_manager.add_module(entry_ns, entry_path)
        World().entry_module = entry_mod
        q: List[Tuple[Namespace, IRModule]] = [(entry_ns, entry_mod)]
        g = ModuleGraph()
        g.add_node(entry_mod)
        while len(q) > 0:
            ns, mod = q.pop()
            ns: Namespace
            mod: IRModule
            # Preprocess module
            # TODO to be completed
            self.analysis_manager.analysis("three address", mod)
            self.analysis_manager.analysis("block cfg", mod)
            self.analysis_manager.analysis("cfg", mod)
            self.analysis_manager.analysis("ssa", mod)
            imports = self.analysis_manager.get_results("block cfg")[mod]

            for stmt in imports:
                get_import = World().namespace_manager.get_import(ns, stmt)
                if get_import is None:
                    continue
                mod_ns, mod_path = get_import
                new_mod = World().scope_manager.add_module(mod_ns, mod_path)
                g.add_edge(mod, new_mod)
                q.append((mod_ns, new_mod))
        World().scope_manager.set_module_graph(g)

    def analyse_intra_procedure(self, analyzer):
        module_graph = World().scope_manager.get_module_graph()
        q = Queue()
        for entry in module_graph.get_entries():
            q.put(entry)
        visited = {*()}
        while not q.empty():

            cur_module = q.get()
            print(cur_module)
            self.analysis_manager.do_analysis(analyzer, cur_module)
            visited.add(cur_module)
            for succ in module_graph.succs_of(cur_module):
                if not succ in visited:
                    q.put(succ)

    def analyse_inter_procedure(self, analyzer: AnalysisDriver):
        entry_mod = World().get_entry_module()
        self.analysis_manager.do_analysis(analyzer, entry_mod)


    def do_transform(self, analyzer):
        module_graph = World().scope_manager.get_module_graph()
        q = Queue()
        for entry in module_graph.get_entries():
            q.put(entry)
        visited = {*()}
        while not q.empty():
            cur_module = q.get()
            self.analysis_manager.do_transform(analyzer, cur_module)
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
        analyzer_generator = self.analysis_manager.generator()
        self.do_analysis(analyzer_generator)
