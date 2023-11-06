from abc import ABC, abstractmethod
from typing import Set, Union, List, Optional
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRAstStmt", "Phi", "Label", "Goto", "JumpIfTrue", "JumpIfFalse"]


class IRAstStmt(IRAbstractStmt):
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

    def get_ast(self) -> Statement:
        return self.stmt

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

    def get_ast(self):
        return None

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

    def get_ast(self):
        return None


'''
  catch exp as e from Label_1 to Label_2 goto Label_3
'''
class IRCatchException(IRAbstractStmt):
    exception: str
    name: str
    from_label: Label
    to_label: Label
    goto_label: Label
    exception_ast: ast.ExceptHandler

    def __init__(self, exception: str, name: str,
                 from_label: Label, to_label: Label, goto_label: Label,
                 exception_ast: ast.ExceptHandler):
        self.exception = exception
        self.name = name
        self.from_label = from_label
        self.to_label = to_label
        self.goto_label = goto_label
        self.exception_ast = exception_ast

    def __str__(self):
        return "pass"

    def get_ast(self):
        return self.pass_ast


class Goto(IRAbstractStmt):
    label: Optional[Label]

    def __init__(self, label=None):
        self.label = label

    def __str__(self):
        return f"goto {self.label.to_s()}"

    def set_label(self, label):
        self.label = label

    def get_ast(self):
        return None


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
        self.test = renamer.visit(self.test)

    def get_ast(self):
        return None


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

    def get_ast(self):
        return None
