from typing import *
import ast
from abc import ABC, abstractmethod

from .statements import *
from .cfg import ControlFlowGraph

__all__ = ["IRScope", "IRClass", "IRFunc", "IRModule", ]


class IRScope(ABC):
    funcs: List['IRFunc']
    classes: List['IRClass']
    imports: List[IRImport]
    cfg: Optional['ControlFlowGraph']

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


class IRClass(IRScope):
    class_def: IRClassDef
    scope: Optional[IRScope]

    def __init__(self, class_def, cfg=None, scope=None,
                 funcs=None, classes=None, imports=None):
        super().__init__(cfg, funcs, classes, imports)
        self.class_def = class_def
        self.scope = scope

    def set_scope(self, scope):
        self.scope = scope

    def get_name(self) -> str:
        return f'cls${self.class_def.name}'

    def __repr__(self) -> str:
        return str(self.class_def)


class IRFunc(IRScope):
    func_def: IRFuncDef
    scope: Optional[IRScope]

    def __init__(self, func_def, cfg=None, scope=None,
                 funcs=None, classes=None, imports=None):
        super().__init__(cfg, funcs, classes, imports)
        self.func_def = func_def
        self.scope = scope

    def set_scope(self, scope):
        self.scope = scope

    def get_name(self) -> str:
        return f'fn${self.func_def.name}'

    def __repr__(self) -> str:
        return ast.unparse(self.func_def.to_ast())


class IRModule(IRScope):
    filename: str

    def __init__(self, cfg=None, filename="", funcs=None, classes=None, imports=None):
        super().__init__(cfg, funcs, classes, imports)
        self.filename = filename

    def get_name(self) -> str:
        return 'mod'

    def __str__(self):
        cls_str = '\n\n'.join([str(c) for c in self.classes])
        fn_str = '\n\n'.join([str(f) for f in self.funcs])
        return '\n'.join([str(self.cfg), cls_str, fn_str])
