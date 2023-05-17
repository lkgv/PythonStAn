from typing import List, Dict, Optional, Tuple

from pythonstan.graph.cfg import IRScope
from pythonstan.utils.common import Singleton
from pythonstan.ir import IRModule
from pythonstan.ir.three_address import ThreeAddressTransformer
from pythonstan.graph.cfg.builder import CFGBuilder
from .namespace import Namespace, NamespaceManager
from .classes import ClassManager
from .modules import ModuleManager
from .analysis_manager import AnalysisManager
from .scope_manager import ScopeManager, ModuleGraph
from .config import Config


class World(Singleton):
    analysis_manager: AnalysisManager
    cls_manager: ClassManager
    mod_manager: ModuleManager
    namespace_manager: NamespaceManager
    entrypoints: List[Tuple[Namespace, IRModule]]
    exec_module_dag: Dict[IRScope, List[IRScope]]
    three_address_builder: ThreeAddressTransformer
    cfg_builder: CFGBuilder

    @classmethod
    def setup(cls):
        cls.cls_manager = ClassManager()
        cls.mod_manager = ModuleManager()
        cls.analysis_manager = AnalysisManager()
        cls.scope_manager = ScopeManager()
        cls.namespace_manager = NamespaceManager()
        cls.entrypoints = []
        cls.three_address_builder = ThreeAddressTransformer()
        cls.cfg_builder = CFGBuilder()

    def add_entry(self, scope: IRScope):
        self.entrypoints.append(scope)

    def get_entries(self) -> List[IRScope]:
        return self.entrypoints

    def build(self, config: Config):
        entry_path = config.filename
        entry_ns = Namespace.build(["__main__"])
        entry_mod = self.scope_manager.add_module(entry_ns, entry_path)
        self.add_entry((entry_ns, entry_mod))
        self.build_scope_graph()
        self.analysis_manager.build(config.get_analysis_list())
        self.scope_manager.build(config.filename, config.project_path)
        self.namespace_manager.build(config.project_path, config.library_paths)

    # import a.b.c : only import packages(so no sub_namespace exists)
    # from ... also the ... is package
    # but problem: from a.b import c; a.func() exists
    def build_scope_graph(self):
        q = [* self.entrypoints]
        g = ModuleGraph()
        while len(q) > 0:
            ns, mod = q.pop()
            ns: Namespace
            mod: IRModule

            # Preprocess module
            # TODO to be completed
            three_address = self.three_address_builder.visit(mod.ast)
            cfg, imports = self.cfg_builder.build_module(three_address)

            self.scope_manager.set_ir(mod, "three-address", three_address)
            self.scope_manager.set_ir(mod, "cfg", cfg)
            for stmt in imports:
                get_import = self.namespace_manager.get_import(ns, stmt)
                if get_import is None:
                    continue
                mod_ns, mod_path = get_import
                new_mod = self.scope_manager.add_module(mod_ns, mod_path)
                g.add_edge(mod, new_mod)
                q.append((mod_ns, new_mod))
        self.scope_manager.set_module_graph(g)
