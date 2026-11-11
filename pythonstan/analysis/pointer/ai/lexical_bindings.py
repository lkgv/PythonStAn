"""Lexical local bindings for name lookup, without entering nested scopes.

IRScope.global_vars records declarations, not all implicitly global reads. This
small syntax service distinguishes a genuinely free name from a local binding;
it does not evaluate imports, comprehensions, defaults, or annotations.
"""
import ast


class _Bindings(ast.NodeVisitor):
    def __init__(self):
        self.names = set()
        self.declared_outer = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Global(self, node):
        self.declared_outer.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_Import(self, node):
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node):
        self.names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")

    def visit_ExceptHandler(self, node):
        if node.name is not None:
            self.names.add(node.name)
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node):
        if node.name:
            self.names.add(node.name)
        if node.pattern:
            self.visit(node.pattern)

    def visit_MatchStar(self, node):
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node):
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)

    def _definition_expressions(self, node):
        for expression in node.decorator_list:
            self.visit(expression)
        for expression in (*node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                         node.args.vararg, node.args.kwarg):
            if argument is not None and argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)
        self._definition_expressions(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.names.add(node.name)
        for expression in (*node.bases, *node.decorator_list):
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node):
        for expression in (*node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)

    def _comprehension(self, node):
        # Iteration targets are local to the comprehension. Assignment
        # expressions in its expressions bind the containing non-comprehension
        # scope (except inside a nested lambda/function, handled above).
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension


def local_bindings(scope):
    """Return bindings owned by a function/class body, including parameters."""
    visitor = _Bindings()
    arguments = getattr(scope, "args", None)
    if arguments is not None:
        visitor.names.update(argument.arg for argument in
                             (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                              arguments.vararg, arguments.kwarg) if argument is not None)
    for statement in scope.body:
        visitor.visit(statement)
    return frozenset(visitor.names - visitor.declared_outer)
