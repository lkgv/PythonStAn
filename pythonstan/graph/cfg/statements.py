from abc import ABC, abstractmethod
from typing import Set, Union, List
import ast
from ast import stmt as Statement

from pythonstan.utils.var_collector import VarCollector

__all__ = ["CFGStmt", "CFGImport", "CFGClassDef", "CFGFuncDef", "CFGAsyncFuncDef", "CFGAstStmt",
           "Label", "Goto", "JumpIfTrue", "JumpIfFalse"]


class CFGStmt(ABC):
    @abstractmethod
    def __str__(self) -> str:
        ...

    def __repr__(self):
        return self.__str__()

    @abstractmethod
    def get_stores(self) -> Set[str]:
        ...

    @abstractmethod
    def get_loads(self) -> Set[str]:
        ...

    @abstractmethod
    def get_dels(self) -> Set[str]:
        ...

    def get_nostores(self) -> Set[str]:
        return self.get_stores().union(self.get_dels())

class AbstractCFGStmt(CFGStmt, ABC):
    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}


class CFGImport(AbstractCFGStmt):
    stmt: Union[ast.Import, ast.ImportFrom]

    def __init__(self, stmt):
        self.stmt = stmt

    def __str__(self):
        ast.unparse(self.stmt)


class CFGClassDef(AbstractCFGStmt):
    name: str
    bases: List[ast.expr]
    keywords: List[ast.keyword]
    decorator_list: List[ast.expr]
    ast_repr: ast.ClassDef

    cell_vars: Set[str]

    def __init__(self, cls: ast.ClassDef, cell_vars=None):
        self.name = cls.name
        self.bases = cls.bases
        self.keywords = cls.keywords
        self.decorator_list = cls.decorator_list
        self.ast_repr = ast.ClassDef(
            name=self.name,
            bases=self.bases,
            keywords=self.keywords,
            body=[],
            decorator_list=self.decorator_list)
        ast.copy_location(self.ast_repr, cls)
        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def to_ast(self) -> ast.ClassDef:
        return self.ast_repr

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n"
        return cell_comment + ast.unparse(self.to_ast())


class CFGFuncDef(AbstractCFGStmt):
    name: str
    args: ast.arguments
    decorator_list: List[ast.expr]
    returns: ast.expr
    type_comment: str
    ast_repr: ast.FunctionDef

    cell_vars: Set[str]

    def __init__(self, fn: ast.FunctionDef, cell_vars=None):
        self.name = fn.name
        self.args = fn.args
        self.decorator_list = fn.decorator_list
        self.returns = fn.returns
        self.type_comment = fn.type_comment
        self.ast_repr = ast.FunctionDef(
            name=self.name,
            args=self.args,
            body=[],
            decorator_list=self.decorator_list,
            returns=self.returns,
            type_comment=self.type_comment)
        ast.copy_location(self.ast_repr, fn)

        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def to_ast(self) -> ast.FunctionDef:
        return self.ast_repr

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n"
        return cell_comment + ast.unparse(self.to_ast())


class CFGAsyncFuncDef(CFGFuncDef):
    ast_repr: ast.AsyncFunctionDef

    def __init__(self, fn: ast.AsyncFunctionDef, cell_vars=None):
        self.name = fn.name
        self.args = fn.args
        self.decorator_list = fn.decorator_list
        self.returns = fn.returns
        self.type_comment = fn.type_comment
        self.ast_repr = ast.AsyncFunctionDef(
            name=self.name,
            args=self.args,
            body=[],
            decorator_list=self.decorator_list,
            returns=self.returns,
            type_comment=self.type_comment)
        ast.copy_location(self.ast_repr, fn)

        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars


class CFGAstStmt(CFGStmt):
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector
    del_collector: VarCollector

    def __init__(self, stmt: Statement):
        self.set_stmt(stmt)

    def __str__(self):
        return ast.unparse(self.stmt)

    def set_stmt(self, stmt: Statement):
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.collector_reset()
        self.collect_from_stmt(stmt)

    def collector_reset(self):
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")
        self.del_collector = VarCollector("del")

    def collect_from_stmt(self, stmt: Statement):
        self.store_collector.visit(stmt)
        self.load_collector.visit(stmt)
        self.del_collector.visit(stmt)

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_dels(self) -> Set[str]:
        return self.del_collector.get_vars()


class Label(AbstractCFGStmt):
    idx: int

    def __init__(self, idx):
        self.idx = idx

    def __str__(self):
        return f"label_{self.idx}:"


class Goto(AbstractCFGStmt):
    label: Label

    def __init__(self, label):
        self.label = label

    def __str__(self):
        return f"goto {self.label}"


class JumpIfFalse(AbstractCFGStmt):
    test: ast.expr
    label: Label

    def __init__(self, test, label):
        self.test = test
        ast.fix_missing_locations(self.test)
        self.label = label

    def __str__(self):
        test_str = ast.unparse(self.test)
        return f"if not ({test_str}) goto {self.label}"


class JumpIfTrue(AbstractCFGStmt):
    test: ast.expr
    label: Label

    def __init__(self, test, label):
        self.test = test
        ast.fix_missing_locations(self.test)
        self.label = label

    def __str__(self):
        test_str = ast.unparse(self.test)
        return f"if not ({test_str}) goto {self.label}"
