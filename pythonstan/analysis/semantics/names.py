"""Lexical binding classification, independent of an analysis engine."""
import ast

def classify_bindings(node):
    local=set();global_names=set();nonlocal_names=set();has_yield=False
    args=getattr(node,"args",None)
    if args:
        local.update(x.arg for x in (*args.posonlyargs,*args.args,*args.kwonlyargs))
        if args.vararg: local.add(args.vararg.arg)
        if args.kwarg: local.add(args.kwarg.arg)
    class Scan(ast.NodeVisitor):
        def visit_Global(self,n): global_names.update(n.names)
        def visit_Nonlocal(self,n): nonlocal_names.update(n.names)
        def visit_Name(self,n):
            if isinstance(n.ctx,(ast.Store,ast.Del)): local.add(n.id)
        def visit_FunctionDef(self,n): local.add(n.name)
        visit_AsyncFunctionDef=visit_FunctionDef
        def visit_ClassDef(self,n): local.add(n.name)
        def visit_Lambda(self,n): pass
        def visit_Import(self,n):
            for alias in n.names: local.add(alias.asname or alias.name.split('.')[0])
        def visit_ImportFrom(self,n):
            for alias in n.names:
                if alias.name!='*':local.add(alias.asname or alias.name)
        def visit_ExceptHandler(self,n):
            if n.name:local.add(n.name)
            for statement in n.body:self.visit(statement)
        def visit_Yield(self,n):
            nonlocal has_yield
            has_yield=True
            if n.value:self.visit(n.value)
        visit_YieldFrom=visit_Yield
    scan=Scan()
    body=getattr(node,"body",())
    if isinstance(body,ast.AST): scan.visit(body)
    else:
        for statement in body:scan.visit(statement)
    return frozenset(local-global_names-nonlocal_names),frozenset(global_names),frozenset(nonlocal_names),has_yield
