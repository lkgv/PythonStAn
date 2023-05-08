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
