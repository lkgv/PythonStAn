from typing import Set, Optional, Dict

from .context import Context, ContextSensitive
from .elements import Var
from pythonstan.ir import IRScope


class SymbolTable:
    _mem: Dict[str, Var]

    def __init__(self):
        self._mem = {}

    def __contains__(self, item: str) -> bool:
        return item in self._mem

    def get(self, name: str) -> Var:
        return self._mem[name]

    def set(self, name: str, var: Var):
        self._mem[name] = var


class PtCode(ContextSensitive):
    _ir: IRScope

    def __init__(self, ir: IRScope, ctx: Context):
        self._ir = ir
        self.set_context(ctx)

    def get_ir(self) -> IRScope:
        return self._ir

    def __eq__(self, other):
        if not isinstance(other, PtCode):
            return False
        return self.get_context() == other.get_context() and self.get_ir() == other.get_ir()

    def __hash__(self):
        return hash((self.get_context(), self.get_ir()))


class PtFrame(ContextSensitive):
    _locals: SymbolTable
    _globals: SymbolTable
    _writable_globals: Set[str]
    _ir: IRScope

    def __init__(self, ir: IRScope, ctx: Context):
        self._locals = SymbolTable()
        self._globals = SymbolTable()
        self._writable_globals = set()
        self._ir = ir
        self.set_context(ctx)

    def get_ir(self) -> IRScope:
        return self._ir

    def gen_var(self, name: str, is_global: bool = False) -> Var:
        var = Var(name, self.get_context(), is_global)
        if is_global:
            self._globals.set(name, var)
        else:
            self._locals.set(name, var)
        return var

    def get_var_write(self, name: str) -> Var:
        if name in self._locals:
            return self._locals.get(name)
        elif name in self._globals and name in self._writable_globals:
            return self._globals.get(name)
        else:
            return self.gen_var(name, False)

    def get_var_read(self, name: str) -> Optional[Var]:
        if name in self._locals:
            return self._locals.get(name)
        elif name in self._globals:
            return self._globals.get(name)
        else:
            return None
