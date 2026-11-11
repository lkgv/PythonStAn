"""Source-owned module names, distinct from builtin name lookup fallback."""
import ast
from pathlib import Path


class _Bindings(ast.NodeVisitor):
    def __init__(self):
        self.names = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_Import(self, node):
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split('.')[0])

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name != '*':
                self.names.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.target)
            self.visit(node.value)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.args.defaults,
                           *(v for v in node.args.kw_defaults if v is not None)):
            self.visit(expression)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                         *(a for a in (node.args.vararg, node.args.kwarg) if a is not None)):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.bases,
                           *(k.value for k in node.keywords)):
            self.visit(expression)

    def visit_Lambda(self, node):
        for expression in (*node.args.defaults, *(v for v in node.args.kw_defaults if v is not None)):
            self.visit(expression)

    def _comprehension(self, node):
        # Comprehension targets are local. Assignment expressions explicitly
        # bind in the containing scope, including those in later clauses.
        collector = _NamedExpressions()
        for child in ast.iter_child_nodes(node):
            collector.visit(child)
        self.names.update(collector.names)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension


class _NamedExpressions(_Bindings):
    def visit_Name(self, node):
        pass

    def visit_NamedExpr(self, node):
        self.names.add(node.target.id)
        self.visit(node.value)


def source_export_names(module):
    """Conservative lexical ownership; values still come from real constraints."""
    cached = getattr(module, '_source_export_names', None)
    if cached is not None:
        return cached
    filename = module.get_filename()
    try:
        tree = ast.parse(Path(filename).read_text()) if filename and Path(filename).is_file() else module.get_ast()
    except (OSError, SyntaxError, UnicodeError):
        tree = module.get_ast()
    visitor = _Bindings()
    visitor.visit(tree)
    # Writes under an explicit global declaration in nested source scopes can
    # introduce exports after initialization. Merely reading a global cannot.
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        local = _Bindings()
        globals_written = set()
        class GlobalNames(ast.NodeVisitor):
            def visit_Global(self, node): globals_written.update(node.names)
            def visit_FunctionDef(self, node): pass
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_ClassDef(self, node): pass
        selector = GlobalNames()
        for statement in scope.body:
            selector.visit(statement)
            local.visit(statement)
        visitor.names.update(local.names & globals_written)
    names = visitor.names | {'__name__', '__package__', '__file__', '__doc__', '__loader__', '__spec__', '__builtins__', '__annotations__'}
    module._source_export_names = frozenset(name for name in names if name.isidentifier())
    return module._source_export_names
