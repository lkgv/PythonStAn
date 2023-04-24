from ast import stmt
from typing import Set

from pythonstan.graph.cfg.models import CFGScope
from pythonstan.utils.var_collector import VarCollector
from .analysis import AnalysisConfig, AnalysisDriver
from .dataflow.driver import DataflowAnalysisDriver


class DefUseAnalysis(AnalysisDriver):
    compute_stores: bool
    compute_loads: bool

    def __init__(self, config: AnalysisConfig):
        self.compute_stores = config.options.get('compute-stores',
                                                 default=True)
        self.compute_loads = config.options.get('compute-loads',
                                                default=True)
        rd_config = AnalysisConfig(name="reaching-definition",
                                   id="ReachingDefinitionAnalysis")
        self.rd_analysis = DataflowAnalysisDriver[Set[stmt]](rd_config)
        super().__init__(config)
    
    def analyze(self, scope: CFGScope):
        rd_result = self.rd_analysis.analyze(scope)
        s_colle = VarCollector()
        l_colle = VarCollector()
        stores, loads = {}, {}
        for cur_stmt in scope.cfg.stmts:
            reaching_defs = rd_result['in'][cur_stmt]
            l_colle.reset("no_store")
            l_colle.visit(cur_stmt)
            for load_id in l_colle.get_vars():
                for reaching_def in reaching_defs:
                    s_colle.reset("store")
                    s_colle.visit(reaching_def)
                    for store_id in s_colle.get_vars():
                        if load_id == store_id:
                            if self.compute_stores:
                                if (cur_stmt, load_id) not in stores:
                                    stores[(cur_stmt, load_id)] = {*()}
                                stores[((cur_stmt, load_id))].add(reaching_def)
                            if self.compute_loads:
                                if reaching_def not in loads:
                                    loads[reaching_def] = {*()}
                                loads.add(cur_stmt)
        return stores, loads

            