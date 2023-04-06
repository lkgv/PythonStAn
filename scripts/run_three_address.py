import ast
from pythonstan.three_address import ThreeAddressTransformer

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
    "p = not (nm := (y.g+z)[2].p if g.k else p) and ((v:=p) or q or v.t) and (f and p)"
]


trans = ThreeAddressTransformer()
for src_str in srcs:
    trans.reset()
    src = ast.parse(src_str)
    res = trans.visit(src)
    res = ast.fix_missing_locations(res)

    print(f"\nParse: {{ {src_str} }}")
    # print(ast.dump(res, indent=4))

    print(ast.unparse(res))
