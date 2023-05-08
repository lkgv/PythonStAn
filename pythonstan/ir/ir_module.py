from .ir_scope import IRScope

__all__ = ["IRScope"]


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
