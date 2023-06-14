from abc import ABC, abstractmethod
from typing import Set
import ast

__all__ = ["IRStatement", "IRAbstractStmt"]


class IRStatement(ABC):
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
    def get_ast(self) -> ast.AST:
        ...

    def rename(self, old_name, new_name, ctxs):
        ...


class IRAbstractStmt(IRStatement, ABC):
    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def rename(self, old_name, new_name, ctxs):
        pass
