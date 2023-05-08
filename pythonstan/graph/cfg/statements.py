from abc import ABC, abstractmethod
from typing import Set, Union, List, Optional
import ast
from ast import stmt as Statement

from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRRStatement", "IRImport", "IRClassDef", "IRFuncDef", "CFGAsyncFuncDef", "IRAstStmt",
           "Phi", "Label", "Goto", "JumpIfTrue", "JumpIfFalse"]


class IRRStatement(ABC):
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
        return self.get_loads().union(self.get_dels())

    @abstractmethod
    def rename(self, old_name, new_name, ctxs):
        ...

class IRAbstractStmt(IRRStatement):
    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def rename(self, old_name, new_name, ctxs):
        pass


class IRImport(IRAbstractStmt):
    stmt: Union[ast.Import, ast.ImportFrom]

    def __init__(self, stmt):
        self.stmt = stmt

    def __str__(self):
        ast.unparse(self.stmt)

    def get_stores(self) -> Set[str]:
        stores = {*()}
        for name in self.stmt.names:
            if name.asname is None:
                stores.add(name.name)
            else:
                stores.add(name.asname)
        return stores

    def rename(self, old_name, new_name, ctxs):
        if ast.Store not in ctxs:
            return
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)


class IRClassDef(IRAbstractStmt):
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
        cell_comment = "# closure: (" + ', '.join(names) + ")\n    "
        return cell_comment + ast.unparse(self.to_ast())


class IRFuncDef(IRAbstractStmt):
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
        cell_comment = "# closure: (" + ', '.join(names) + ")\n    "
        return cell_comment + ast.unparse(self.to_ast())


class CFGAsyncFuncDef(IRFuncDef):
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


class IRAstStmt(IRRStatement):
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

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)


class Phi(IRAbstractStmt):
    var: str
    loads: List[str]
    store: str

    def __init__(self, var, store, loads):
        self.var = var
        self.store = store
        self.loads = loads

    def set_store(self, name: str):
        self.store = name

    def set_load(self, idx: int, load: str):
        if 0 <= idx < len(self.loads):
            self.loads[idx] = load
        else:
            raise ValueError("invalid index")

    def get_stores(self) -> Set[str]:
        return {self.store}

    def get_loads(self) -> Set[str]:
        return set(self.loads)

    def rename(self, old_name, new_name, ctxs):
        if ast.Store not in ctxs:
            return
        if self.store == old_name:
            self.set_store(new_name)

    def __str__(self) -> str:
        load_str = ', '.join(self.loads)
        return f"{self.store} = Phi({load_str})"


class Label(IRAbstractStmt):
    idx: int

    def __init__(self, idx):
        self.idx = idx

    def __str__(self):
        return f"label_{self.idx}:"

    def to_s(self):
        return f"label_{self.idx}"


class Goto(IRAbstractStmt):
    label: Optional[Label]

    def __init__(self, label=None):
        self.label = label

    def __str__(self):
        return f"goto {self.label.to_s()}"

    def set_label(self, label):
        self.label = label


class JumpIfFalse(IRAbstractStmt):
    test: ast.expr
    label: Label
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
        print(self.test)
        self.test = renamer.visit(self.test)
        print(self.test)
        print()


class JumpIfTrue(IRAbstractStmt):
    test: ast.expr
    label: Label
    load_collector: VarCollector

    def __init__(self, test, label):
        self.test = test
        ast.fix_missing_locations(self.test)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.test)
        self.label = label

    def __str__(self):
        test_str = ast.unparse(self.test)
        return f"if not ({test_str}) goto {self.label.to_s()}"

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.test = renamer.visit(self.test)
