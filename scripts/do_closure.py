import ast

from pythonstan.ir.three_address import ThreeAddressTransformer
from pythonstan.graph.cfg.builder import CFGBuilder, StmtCFGTransformer
from pythonstan.graph.cfg.models import CFGModule, CFGFunc, CFGClass
from pythonstan.analysis.dataflow import DataflowAnalysisDriver
from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.graph.cfg.visualize import draw_module, new_digraph
from pythonstan.analysis.scope import ClosureAnalysis


ta_trans = ThreeAddressTransformer()
cfg_trans = CFGBuilder()

'''
for src_str in srcs:
    ta_trans.reset()

    src = ast.parse(src_str)
    ta_src = ta_trans.visit(src)
    cfg_mod = cfg_trans.build_module(ta_src.body)

    ta_src = ast.fix_missing_locations(ta_src)

    print(f"\nParse: {{ {src_str} }}")
    # print(ast.dump(res, indent=4))

    print(ast.unparse(ta_src))
    print(cfg_mod)

flist = ['/home/codergwy/code/HiTyper/hityper/tdg.py',
         '/home/codergwy/code/pynguin/pynguin/configuration.py',
]
'''

flist = ['/home/codergwy/code/test/test.py']
config = AnalysisConfig("closure", "ClosureAnalysis", options={'in_place':True})

def out_closure(scope):
    if isinstance(scope, CFGFunc):
        print(f"{scope.get_name()}: {scope.func_def.cell_vars}")
    if isinstance(scope, CFGClass):
        print(f"{scope.get_name()}: {scope.class_def.cell_vars}")
    for cls in scope.classes:
        out_closure(cls)
    for fn in scope.funcs:
        out_closure(fn)

# anal = ClosureAnalysis()
for fname in flist:
    with open(fname, 'r') as f:
        ta_trans.reset()
        src = ast.parse(f.read())
        ta_src = ta_trans.visit(src)
        cfg_mod = cfg_trans.build_module(ta_src.body)
        # cfg_mod = StmtCFGTransformer().trans(cfg_mod)
        anal = ClosureAnalysis(config)
        clos = anal.analyze_module(cfg_mod)
        print(clos)
        out_closure(cfg_mod)

        
