from typing import *
from abc import ABC, abstractmethod

from .ir_statements import *
from pythonstan.graph.cfg import ControlFlowGraph

__all__ = ["IRScope"]


class IRScope(ABC):
    funcs: List['IRFunc']
    classes: List['IRClass']
    imports: List[IRImport]
    cfg: Optional['ControlFlowGraph']
    prev_scope: Optional[IRScope]

    @abstractmethod
    def __init__(self, cfg, funcs, classes, imports):
        if funcs is None:
            funcs = []
        if classes is None:
            classes = []
        if imports is None:
            imports = []
        self.set_cfg(cfg)
        self.set_funcs(funcs)
        self.set_classes(classes)
        self.set_imports(imports)

    def set_funcs(self, funcs: List['IRFunc']):
        self.funcs = funcs

    def set_classes(self, classes: List['IRClass']):
        self.classes = classes

    def set_imports(self, imports: List[IRImport]):
        self.imports = imports

    def set_cfg(self, cfg: ControlFlowGraph):
        self.cfg = cfg

    def add_func(self, func: 'IRFunc'):
        self.funcs.append(func)

    def add_class(self, cls: 'IRClass'):
        self.classes.append(cls)

    def add_import(self, imp: IRImport):
        self.imports.append(imp)

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError
