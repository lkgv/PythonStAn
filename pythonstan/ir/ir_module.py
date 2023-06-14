from ast import Module, parse
from typing import *

from .ir_scope import IRScope
from .ir_statement import IRStatement

__all__ = ["IRModule"]


class IRModule(IRScope, IRStatement):
    name: str
    filename: str
    ast: Module

    def __init__(self, qualname: str, module: Module, name="", filename=None):
        super().__init__(qualname)
        self.name = name
        if filename is None:
            self.filename = "None"
        else:
            self.filename = filename
        self.ast = module

    def get_name(self) -> str:
        return f'<module \'{self.name}\' from \'{self.filename}\'>'

    def get_ast(self) -> Module:
        return self.ast

    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def __str__(self) -> str:
        return self.get_name()

    def __repr__(self) -> str:
        return self.get_name()

    @classmethod
    def load_module(cls, name: str, filename: str, content: Optional[str] = None) -> 'IRModule':
        if content is None:
            with open(filename, 'r') as f:
                content = f.read()
        mod_ast = parse(content, filename)
        mod = cls(mod_ast, name=name, filename=filename)
        return mod
