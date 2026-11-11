from pythonstan.ir import IRModule
from pythonstan.analysis import AnalysisConfig, AnalysisDriver
from .namespace import Namespace
from .world import World
from .config import Config
from .scope_manager import ModuleGraph
from .analysis_manager import AnalysisManager

from typing import List, Tuple, Generator
from collections import deque
import logging

logger = logging.getLogger(__name__)

class Pipeline:
    config: Config
    analysis_manager: AnalysisManager

    def __init__(self, config=None, filename=None):
        if config is not None:
            self.config = config if isinstance(config, Config) else Config.from_dict(config)
        elif filename is not None:
            self.config = Config.from_file(filename)
        else:
            raise ValueError("No proper configuration for pipeline!")
        World().setup()
        World().build(self.config)
        self.analysis_manager = AnalysisManager()
        self.analysis_manager.build(self.config.get_analysis_list())
        from pythonstan.compat.native_services import configure_native_services
        configure_native_services(self.analysis_manager)
        self.analysis_manager.set_time_count(self.config.time_count)
        World().analysis_manager = self.analysis_manager
        self.build_scope_graph(self.config.filename)

    def get_world(self):
        return World()

    def build_scope_graph(self, entry_path: str):
        entry_ns = World().namespace_manager.set_entry_module(entry_path, self.config.project_path)
        entry_mod = World().scope_manager.add_module(entry_ns, entry_path)
        World().entry_module = entry_mod
        q = deque([(entry_ns, entry_mod, 0)])
        g = ModuleGraph()
        g.add_node(entry_mod)
        self._preprocessed_modules = set()
        # Breadth-first traversal gives each module its shortest import depth.
        # Registered imports beyond the depth limit remain visible in the graph,
        # but are not silently run through passes that require missing IR.
        while q:
            ns, mod, level = q.popleft()
            if mod in self._preprocessed_modules:
                continue
            self._preprocessed_modules.add(mod)
            self.analysis_manager.analysis("three address", mod)
            self.analysis_manager.analysis("ir", mod)
            self.analysis_manager.analysis("block cfg", mod)
            self.analysis_manager.analysis("cfg", mod)
            imports = World().scope_manager.get_ir(mod, "imports") or []
            for stmt in imports:
                get_import = World().namespace_manager.get_import(ns, stmt)
                if get_import is not None:
                    mod_ns, mod_path = get_import
                    new_mod = World().scope_manager.add_module(mod_ns, mod_path)
                    if new_mod is None:
                        continue
                    g.add_edge(mod, stmt, new_mod)
                    World().import_manager.set_import(mod, stmt, new_mod)
                    # Importing a dotted module also initializes its package
                    # chain. Keep those real __init__.py modules in the same
                    # arena; the import edge still identifies the leaf module.
                    for end in range(1, len(mod_ns.names)):
                        parent = World().namespace_manager.resolve_import('.'.join(mod_ns.names[:end]))
                        if parent is None:
                            continue
                        parent_ns, parent_path = parent
                        parent_mod = World().scope_manager.add_module(parent_ns, parent_path)
                        if parent_mod is None:
                            continue
                        if parent_mod not in g.get_modules():
                            g.add_node(parent_mod)
                        if (not self.config.lazy_ir_construction
                                and (self.config.import_level < 0 or level < self.config.import_level)):
                            q.append((parent_ns, parent_mod, level + 1))
                    if (not self.config.lazy_ir_construction
                            and (self.config.import_level < 0 or level < self.config.import_level)):
                        q.append((mod_ns, new_mod, level + 1))
        World().scope_manager.set_module_graph(g)
        # ClosureAnalysis traverses all built scopes internally; invoke it once.
        self.analysis_manager.analysis("closure", entry_mod)

    def _modules_once(self):
        """Visit built modules exactly once, including cyclic import graphs."""
        graph = World().scope_manager.get_module_graph()
        modules = sorted(graph.get_modules(), key=lambda mod: mod.get_qualname())
        queue = deque([World().get_entry_module(), *modules])
        seen = set()
        built = getattr(self, "_preprocessed_modules", None)
        while queue:
            module = queue.popleft()
            if module in seen:
                continue
            seen.add(module)
            queue.extend(graph.succs_of(module))
            if built is None or module in built:
                yield module

    def analyse_intra_procedure(self, analyzer):
        for module in self._modules_once():
            self.analysis_manager.do_analysis(analyzer, module)

    def analyse_inter_procedure(self, analyzer: AnalysisDriver):
        entry_mod = World().get_entry_module()
        self.analysis_manager.do_analysis(analyzer, entry_mod)

    def do_transform(self, analyzer):
        for module in self._modules_once():
            self.analysis_manager.do_analysis(analyzer, module)

    def do_analysis(self, analyzer_generator: Generator[AnalysisDriver, None, None]):
        for analyzer in analyzer_generator:
            if analyzer.config.type == "dataflow analysis":
                if analyzer.config.inter_procedure:
                    self.analyse_inter_procedure(analyzer)
                else:
                    self.analyse_intra_procedure(analyzer)
            elif analyzer.config.type == 'transform':
                self.do_transform(analyzer)
            elif analyzer.config.type == 'closure analysis':
                self.analyse_inter_procedure(analyzer)
            elif analyzer.config.type == 'pointer analysis':
                # Pointer analysis is typically inter-procedural
                self.analyse_inter_procedure(analyzer)
            elif analyzer.config.type == 'ai analysis':
                self.analyse_inter_procedure(analyzer)
            elif analyzer.config.type == 'certified analysis':
                self.analyse_inter_procedure(analyzer)
            else:
                raise ValueError(f"Unsupported pipeline analysis type: {analyzer.config.type}")

    def run(self):
        analyzer_generator = self.analysis_manager.generator()
        self.do_analysis(analyzer_generator)
