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
    "p = not (nm := (y.g+z)[2].p if g.k else p) and ((v:=p) or q or v.t) and (f and p)",
    "x = lambda x: (a+(b+c)).q + a.v[xp](a, z=3, *args, **(kwargs+{'z':p}))[:x:-1]",
    "a, (b+p).c, *(p1, *(p2.v, p3)) = a, *(a+b), c",
    "a, [*[(b+c).v, q], t], q = ss",
    "f'fuck[{x}]: {f(x)}'",
    "(a + 7).v : Union[int, str] = (a+b) * 20 - q",
    "del a, (p+q).v, (z.m)[x]"
    ]


trans = ThreeAddressTransformer()
for src_str in srcs:
    trans.reset()
    src = ast.parse(src_str)
    res = trans.visit(src)
    res = ast.fix_missing_locations(res)

    print(f"\nParse: {{ {src_str} }}")
    print(ast.dump(res, indent=4))

    print(ast.unparse(res))