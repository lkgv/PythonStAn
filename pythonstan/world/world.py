from typing import List, Dict, Optional, Tuple

from pythonstan.utils.common import Singleton
from pythonstan.ir import IRModule, IRScope
from pythonstan.ir.three_address import ThreeAddressTransformer
from .namespace import Namespace, NamespaceManager
from .analysis_manager import AnalysisManager
from .scope_manager import ScopeManager, ModuleGraph
from .config import Config


class World(Singleton):
    analysis_manager: AnalysisManager
    namespace_manager: NamespaceManager
    three_address_builder: ThreeAddressTransformer
    entry_module: IRModule

    @classmethod
    def setup(cls):
        cls.analysis_manager = AnalysisManager()
        cls.scope_manager = ScopeManager()
        cls.namespace_manager = NamespaceManager()
        cls.three_address_builder = ThreeAddressTransformer()

    def build(self, config: Config):
        self.scope_manager.build()
        self.namespace_manager.build(config.project_path, config.library_paths)
        self.analysis_manager.build(config.get_analysis_list())
        self.build_scope_graph(config.filename)


    def get_entry_module(self) -> IRModule:
        return self.entry_module

    # import a.b.c : only import packages(so no sub_namespace exists)
    # from ... also the ... is package
    # but problem: from a.b import c; a.func() exists
    def build_scope_graph(self, entry_path: str):
        entry_ns = Namespace.build(["__main__"])
        entry_mod = self.scope_manager.add_module(entry_ns, entry_path)
        self.entry_module = entry_mod
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
                get_import = self.namespace_manager.get_import(ns, stmt)
                if get_import is None:
                    continue
                mod_ns, mod_path = get_import
                new_mod = self.scope_manager.add_module(mod_ns, mod_path)
                g.add_edge(mod, new_mod)
                q.append((mod_ns, new_mod))
        self.scope_manager.set_module_graph(g)
