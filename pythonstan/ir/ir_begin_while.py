import ast
from typing import Set

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer


class IRBeginWhile(IRAbstractStmt):

    ...

    test: ast.expr
    load_collector: VarCollector

    def __init__(self, test, label=None):
        self.test = test
        ast.fix_missing_locations(self.test)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.test)
        if label is None:
            self.label = Label(-1)
        else:
            self.label = label

    def set_label(self, label):
        self.label = label

    def __str__(self):
        test_str = ast.unparse(self.test)
        return f"if not ({test_str}) goto {self.label.to_s()}"

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.test = renamer.visit(self.test)

    def get_ast(self):
        return None