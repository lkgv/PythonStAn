from typing import Set, List, Union, Optional, Tuple
import ast

from .ir_statement import IRAbstractStmt
from pythonstan.utils.ast_rename import RenameTransformer
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRCall"]


class IRCall(IRAbstractStmt):
    stmt: Union[ast.Assign, ast.Call]
    call: ast.Call
    target: Optional[str]
    func_name: str
    args: List[Tuple[str, bool]]
    keywords: List[Tuple[Optional[str], str]]
    load_collector: VarCollector

    def __init__(self, stmt):
        self.stmt = stmt
        if isinstance(stmt, ast.Assign):
            assert isinstance(stmt.targets[0], ast.Name)
            assert isinstance(stmt.value, ast.Call)
            self.call = stmt.value
            self.target = stmt.targets[0].id
        else:
            assert isinstance(stmt, ast.Call)
            self.call = stmt
            self.target = None
        assert isinstance(self.call.func, ast.Name)

        self.func_name = self.call.func.id
        self.args = []
        for arg in self.call.args:
            if isinstance(arg, ast.Name):
                self.args.append((arg.id, False))
            elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                self.args.append((arg.value.id, True))
            else:
                raise AssertionError("Args in function call should be Name or Starred[Name]")
        self.keywords = []
        for kw in self.call.keywords:
            assert isinstance(kw.value, ast.Name), "Keywords in function call should be Name"
            if kw.arg is None:
                self.keywords.append((None, kw.value.id))
            else:
                self.keywords.append((kw.arg, kw.value.id))
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)

    def __str__(self):
        ast.unparse(self.stmt)

    def get_func_name(self) -> str:
        return self.func_name

    def get_args(self) -> List[Tuple[str, bool]]:
        return self.args

    def get_keywords(self) -> List[Tuple[Optional[str], str]]:
        return self.keywords

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_target(self) -> Optional[str]:
        return self.target

    def get_stores(self) -> Set[str]:
        if self.target is not None:
            return {self.target}
        else:
            return {*()}

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)

    def get_ast(self) -> ast.Call:
        return self.stmt
