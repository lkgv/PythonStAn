import ast

from pythonstan.ir.three_address import ThreeAddressTransformer
from pythonstan.graph.cfg.builder import CFGBuilder, StmtCFGTransformer
from pythonstan.analysis.dataflow.driver import DataflowAnalysisDriver
from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.graph.cfg.visualize import draw_module, new_digraph


src = '''
# (a, b) = 3, 4
if a > 10:
  g = a + 4
else:
  g = b
'''

srcs = [
    "q, *[(ast, (gst+p).c),bst], p = not (nm := (y.g+z)[2].p if g.k else p) and ((v:=p) or q or v.t) and (f and p), {a+b, *[(d.v)[3+4:q.v:-1]]}, *(a+c)",
    "return {'a': p.v, [k].str: z, **(gets * q)}",
    "return (yield a[k].z[p.q[m]])",
    "return a.p + q > (b + c) < (c.d) > (c.d[e])",
    "p = not (nm := (y.g+z)[2].p if g.k else p) and ((v:=p) or q or v.t) and (f and p)",
    "x = lambda x: (a+(b+c)).q + a.v[xp](a, z=3, *args, **(kwargs+{'z':p}))[:x:-1]",
    "a, (b+p).c, *(p1, *(p2.v, p3)) = a, *(a+b), c",
    "a, [*[(b+c).v, q], t], q = ss",
    "f'fuck[{x}]: {f(x)}'",
    "(a + 7).v : Union[int, str] = (a+b) * 20 - q",
    "del a, (p+q).v, (z.m)[x]",
    '''
if a and b and (p or q):
    do()
else:
    else_do()
    ''',
    '''
while a and b or c:
    do_while()
else:
    do_else()
    ''',
    '''
for x, y, *(p, q) in range(x+y):
    do_for()
else:
    do_else()
    ''',
    '''
with gen() as (a, b), q() as (f, *(g, q)):
    do()
    do(None)
    ''',
    '''
assert True, "abc"
    ''',
    '''
import a, b as k
from .ast import (a, b, c as ac)
from .ast import *

try:
  def f(a): return a * a
  q = lambda x: (lambda y: y + x) (x + 1)
  p = a + b
except RuntimeException as re:
  re_handler()
except TypeError as te:
  te_handler()
else:
  el_handler()
finally:
  try_fin()

@decorator1
class A(base1, base2, metaclass=meta):
  a = 20
  b = a + 30
  def f(self, x):
    return x + a + b
p = {p(x, y+1) for x, (t, y), *args in p if x for y in ast if z if y  if (p + z) for t in xz}
q = {k:(v*20) for k, v in dlist}
w = (k for k, p in x)
    '''
    ]


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
config = AnalysisConfig("liveness", "LivenessAnalysis",
                        options={'solver': 'WorklistSolver'})
df_driver = DataflowAnalysisDriver(config)

for fname in flist:
    with open(fname, 'r') as f:
        ta_trans.reset()
        src = ast.parse(f.read())
        ta_src = ta_trans.visit(src)
        cfg_mod = cfg_trans.build_module(ta_src.body)
        # cfg_mod = StmtCFGTransformer().trans(cfg_mod)
        df_driver.analyze(cfg_mod)
        results = df_driver.results
        info = {}
        for blk in results['in'].keys():
            res_in = results['in'].get(blk, {*()})
            if len(res_in) == 0:
                res_in = ""
            res_out = results['out'].get(blk, {*()})
            if len(res_out) == 0:
                res_out = ""
            info[blk] = f"{res_in} | {res_out}"
        # print(results)
        g = new_digraph('G', filename='/home/codergwy/code/test/test_cfg.gv')
        draw_module(cfg_mod, g, info)
        g.view()
