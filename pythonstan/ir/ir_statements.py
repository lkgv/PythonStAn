from abc import ABC, abstractmethod
from typing import Set, Union, List, Optional, Tuple
import ast
from ast import stmt as Statement
from ast import Module, ClassDef

from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer


__all__ = [
    'IRStatement',
    'IRAbstractStmt',
    'IRAstStmt',
    'Label',
    'IRCatchException',
    'Goto',
    'JumpIfFalse',
    'JumpIfTrue',
    'IRYield',
    'IRReturn',
    'IRRaise',
    'IRPass',
    'IRAwait',
    'IRDel',
    'IRImport',
    'IRCall',
    'IRAnno',
    'AbstractIRAssign',
    'IRAssign',
    'IRStoreAttr',
    'IRLoadAttr',
    'IRStoreSubscr',
    'IRLoadSubscr',
    'IRPhi',
    'IRScope',
    'IRModule',
    'IRFunc',
    'IRClass'
]


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

    def __lt__(self, other: 'IRStatement') -> bool:
        return str(self) < str(other)


class IRAbstractStmt(IRStatement, ABC):
    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def rename(self, old_name, new_name, ctxs):
        pass


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
  catch exp from Label_1 to Label_2 goto Label_3
'''
class IRCatchException(IRAbstractStmt):
    exception: List[str]
    from_label: Label
    to_label: Label
    goto_label: Label
    exception_ast: Optional[ast.ExceptHandler]

    def __init__(self, exception: List[str],
                 from_label: Label, to_label: Label, goto_label: Label,
                 exception_ast: Optional[ast.ExceptHandler] = None):
        self.exception = exception
        self.from_label = from_label
        self.to_label = to_label
        self.goto_label = goto_label
        self.exception_ast = exception_ast

    def __str__(self):
        return f"catch {self.exception} from {self.from_label} to {self.to_label} with {self.goto_label}"

    def get_ast(self):
        return self.exception_ast


# goto label
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


# if not test goto label
class JumpIfFalse(IRAbstractStmt):
    test: ast.expr
    label: Label
    stmt_ast: Optional[ast.stmt]
    load_collector: VarCollector

    def __init__(self, test, label=None, stmt_ast=None):
        self.test = test
        ast.fix_missing_locations(self.test)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.test)
        if label is None:
            self.label = Label(-1)
        else:
            self.label = label
        self.stmt_ast = stmt_ast

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
        return self.stmt_ast


# if test goto label
class JumpIfTrue(IRAbstractStmt):
    test: ast.expr
    label: Label
    load_collector: VarCollector
    stmt_ast: Optional[ast.stmt]

    def __init__(self, test, label, stmt_ast = None):
        self.test = test
        ast.fix_missing_locations(self.test)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.test)
        self.label = label
        self.stmt_ast = stmt_ast

    def __str__(self):
        test_str = ast.unparse(self.test)
        return f"if not ({test_str}) goto {self.label.to_s()}"

    def set_label(self, label):
        self.label = label

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.test = renamer.visit(self.test)

    def get_ast(self):
        return self.stmt_ast


# [target] = yield value
class IRYield(IRAbstractStmt):
    stmt: Statement
    value: Optional[ast.Name]
    load_collector: VarCollector
    store_collector: VarCollector
    target: Optional[ast.expr]
    _is_yield_from: bool

    def __init__(self, stmt):
        self.stmt = stmt
        assert isinstance(stmt.value, (ast.Yield, ast.YieldFrom))
        self.target = stmt.targets[0] if isinstance(stmt, ast.Assign) else None
        yield_value = stmt.value.value
        if yield_value is None:
            self.value = None
        else:
            assert isinstance(yield_value, ast.Name)
            self.value = yield_value
        self._is_yield_from = isinstance(self.value, ast.YieldFrom)
        ast.fix_missing_locations(self.stmt)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)
        self.store_collector = VarCollector("store")
        self.store_collector.visit(self.stmt)

    def __str__(self):
        if self.value is not None:
            val_str = ast.unparse(self.value)
            return f"yield {val_str}"
        else:
            return "yield"

    def is_yield_from(self) -> bool:
        return self._is_yield_from

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.value)

    def get_ast(self):
        return self.stmt


# return [value]
class IRReturn(IRAbstractStmt):
    value: Optional[ast.Name]
    stmt: ast.stmt
    load_collector: VarCollector

    def __init__(self, stmt: ast.Return):
        ast.fix_missing_locations(stmt)
        if stmt.value is not None:
            assert isinstance(stmt.value, ast.Name), "Return value of IR should be ast.Name or None!"
            self.value = stmt.value
        else:
            self.value = None
        self.stmt = stmt
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)

    def __str__(self):
        return ast.unparse(self.stmt)

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_value(self) -> Optional[str]:
        return self.value

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.stmt)

    def get_ast(self):
        return self.value


# raise exc from cause
class IRRaise(IRAbstractStmt):
    exc: Optional[ast.expr]
    cause: Optional[ast.expr]
    stmt: ast.Raise
    load_collector: VarCollector

    def __init__(self, stmt: ast.Raise):
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.exc = stmt.exc
        self.cause = stmt.cause
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)

    def __str__(self):
        return ast.unparse(self.stmt)

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if self.exc is not None:
            self.exc = renamer.visit(self.exc)
        if self.cause is not None:
            self.cause = renamer.visit(self.cause)

    def get_ast(self):
        return self.stmt


# pass
class IRPass(IRAbstractStmt):
    pass_ast: ast.Pass

    def __init__(self, pass_ast=None):
        if pass_ast is None:
            self.pass_ast = ast.Pass()
        else:
            self.pass_ast = pass_ast

    def __str__(self):
        return "pass"

    def get_ast(self):
        return self.pass_ast


# [target] = await value
class IRAwait(IRAbstractStmt):
    stmt: Statement    
    value: ast.expr
    load_collector: VarCollector
    store_collector: VarCollector
    target: Optional[ast.Name]

    def __init__(self, stmt):
        self.stmt = stmt
        if isinstance(stmt, ast.Assign):
            assert isinstance(stmt.value, ast.Await)
            self.value = stmt.value.value
            self.target = stmt.targets[0]
        else:
            assert isinstance(stmt.value, ast.Await)
            self.value = stmt.value.value
            self.target = None
        ast.fix_missing_locations(self.stmt)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)
        self.store_collector = VarCollector("store")
        self.store_collector.visit(self.stmt)

    def __str__(self):
        return ast.unparse(self.stmt)
    
    def get_target(self) -> Optional[ast.Name]:
        return self.target
    
    def get_value(self) -> ast.expr:
        return self.value

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.value)

    def get_ast(self):
        return self.stmt


# delete value
class IRDel(IRAbstractStmt):
    value: ast.Name
    del_collector: VarCollector

    def __init__(self, stmt: ast.Delete):
        assert len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
        ast.fix_missing_locations(stmt)
        self.value = stmt.targets[0]
        self.del_collector = VarCollector("del")
        self.del_collector.visit(self.value)

    def __str__(self):
        val_str = ast.unparse(self.value)
        return f"del {val_str}"

    def get_dels(self) -> Set[str]:
        return self.del_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.value)

    def get_ast(self):
        return self.value


# import [module.]name as [asname]
class IRImport(IRAbstractStmt):
    stmt: Union[ast.Import, ast.ImportFrom]
    module: Optional[str]
    name: str
    asname: Optional[str]
    level: int

    def __init__(self, stmt: Union[ast.Import, ast.ImportFrom]):
        self.stmt = stmt
        if isinstance(stmt, ast.ImportFrom):
            if stmt.module is not None:
                self.module = stmt.module
            else:
                self.module = ""
            self.level = stmt.level
        else:
            self.module = None
            self.level = 0
        self.name = stmt.names[0].name
        self.asname = stmt.names[0].asname

    def __str__(self):
        return ast.unparse(self.stmt)

    def __repr__(self):
        return ast.unparse(self.stmt)

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

    def get_ast(self) -> Union[ast.Import, ast.ImportFrom]:
        return self.stmt


# [target] = func_name( args, keywords )
class IRCall(IRAbstractStmt):
    stmt: Union[ast.Assign, ast.Call]
    call: ast.Call
    target: Optional[str]
    func_name: str
    args: List[Tuple[str, bool]]
    keywords: List[Tuple[Optional[str], str]]
    load_collector: VarCollector

    def __init__(self, stmt):
        self.stmt = stmt
        if isinstance(stmt, ast.Assign):
            assert isinstance(stmt.targets[0], ast.Name)
            assert isinstance(stmt.value, ast.Call)
            self.call = stmt.value
            self.target = stmt.targets[0].id
        else:
            assert isinstance(stmt, ast.Call)
            self.call = stmt
            self.target = None

        assert isinstance(self.call.func, ast.Name), ast.dump(self.call, indent=4)

        self.func_name = self.call.func.id
        self.args = []
        for arg in self.call.args:
            if isinstance(arg, ast.Name):
                self.args.append((arg.id, False))
            elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                self.args.append((arg.value.id, True))
            elif isinstance(arg, ast.Constant):
                self.args.append((f'<Constant: {str(arg.value)}>', False))
            else:
                raise AssertionError("Args in function call should be Name or Starred[Name]")
        self.keywords = []
        for kw in self.call.keywords:
            assert isinstance(kw.value, ast.Name), "Keywords in function call should be Name"
            if kw.arg is None:
                self.keywords.append((None, kw.value.id))
            else:
                self.keywords.append((kw.arg, kw.value.id))
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)

    def __str__(self):
        return ast.unparse(self.stmt)

    def get_func_name(self) -> str:
        return self.func_name

    def get_args(self) -> List[Tuple[str, bool]]:
        return self.args

    def get_keywords(self) -> List[Tuple[Optional[str], str]]:
        return self.keywords

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_target(self) -> Optional[str]:
        return self.target

    def get_stores(self) -> Set[str]:
        if self.target is not None:
            return {self.target}
        else:
            return {*()}

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)

    def get_ast(self) -> ast.Call:
        return self.stmt


# target: anno
class IRAnno(IRAbstractStmt):
    target: ast.expr
    anno: ast.expr
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.AnnAssign):
        self.set_stmt(stmt)

    def __str__(self):
        tgt_str = ast.unparse(self.target)
        ann_str = ast.unparse(self.anno)
        return f"{tgt_str} : {ann_str}"

    def set_stmt(self, stmt: ast.AnnAssign):
        self.target = stmt.target
        self.anno = stmt.annotation
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.collector_reset()
        self.collect_from_stmt(stmt)

    def get_ast(self) -> Statement:
        return self.stmt

    def get_target(self) -> ast.expr:
        return self.target

    def get_anno(self) -> ast.expr:
        return self.anno

    def collector_reset(self):
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")

    def collect_from_stmt(self, stmt: Statement):
        self.store_collector.visit(self.target)
        self.load_collector.visit(self.anno)

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if isinstance(ast.Load(), ctxs):
            self.anno = renamer.visit(self.anno)
        if isinstance(ast.Store(), ctxs):
            self.target = renamer.visit(self.target)


class AbstractIRAssign(IRAbstractStmt):
    lval: ast.expr
    rval: ast.expr
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    @abstractmethod
    def __init__(self, stmt: ast.Assign):
        assert len(stmt.targets) == 1, "IRAssign only supports single target assignment"
        self.set_stmt(stmt)

    def __str__(self):
        lstr = ast.unparse(self.lval)
        rstr = ast.unparse(self.rval)
        return f"{lstr} = {rstr}"

    def set_stmt(self, stmt: ast.Assign):
        self.lval = stmt.targets[0]
        self.rval = stmt.value
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.collector_reset()
        self.collect_from_stmt(stmt)

    def get_ast(self) -> Statement:
        return self.stmt

    def get_lval(self) -> ast.expr:
        return self.lval

    def get_rval(self) -> ast.expr:
        return self.rval

    def collector_reset(self):
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")

    def collect_from_stmt(self, stmt: Statement):
        self.store_collector.visit(self.lval)
        self.load_collector.visit(self.rval)

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if isinstance(ast.Load(), ctxs):
            self.rval = renamer.visit(self.rval)
        if isinstance(ast.Store(), ctxs):
            self.lval = renamer.visit(self.lval)


# lval = rval
class IRAssign(AbstractIRAssign):
    lval: ast.Name
    rval: ast.Name
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        assert isinstance(stmt.targets[0], ast.Name)
        assert isinstance(stmt.value, ast.Name)
        self.set_stmt(stmt)

    def get_lval(self) -> ast.Name:
        return self.lval

    def get_rval(self) -> ast.Name:
        return self.rval


# lval (obj.attr) = rval
class IRStoreAttr(AbstractIRAssign):
    rval: ast.Name
    obj: ast.Name
    attr: str

    def __init__(self, stmt: ast.Assign):
        super().__init__(stmt)

        target = stmt.targets[0]
        assert isinstance(target, ast.Attribute)
        assert isinstance(target.value, ast.Name)
        assert isinstance(stmt.value, ast.Name)
        self.obj = target.value
        self.attr = target.attr

    def get_rval(self) -> ast.Name:
        return self.rval

    def get_obj(self) -> ast.Name:
        return self.obj

    def get_attr(self) -> str:
        return self.attr


# lval = rval( obj.attr )
class IRLoadAttr(AbstractIRAssign):
    lval: ast.Name
    obj: ast.Name
    attr: str

    def __init__(self, stmt: ast.Assign):
        super().__init__(stmt)

        assert isinstance(stmt.targets[0], ast.Name)
        assert isinstance(stmt.value, ast.Attribute)
        assert isinstance(stmt.value.value, ast.Name)
        self.obj = stmt.value.value
        self.attr = stmt.value.attr

    def get_lval(self) -> ast.Name:
        return self.lval

    def get_obj(self) -> ast.Name:
        return self.obj

    def get_attr(self) -> str:
        return self.attr


# lval< obj[slice] > = rval
class IRStoreSubscr(AbstractIRAssign):
    lval: ast.Subscript
    rval: ast.Name
    obj: ast.Name
    subslice: ast.Slice

    def __init__(self, stmt: ast.Assign):
        super().__init__(stmt)

        target = stmt.targets[0]
        assert isinstance(target, ast.Subscript)
        assert isinstance(target.value, ast.Name)
        assert isinstance(target.slice, ast.Slice)
        assert isinstance(stmt.value, ast.Name)
        self.obj = target.value
        self.subslice = target.slice
    
    def get_rval(self) -> ast.Name:
        return self.rval

    def get_obj(self) -> ast.Name:
        return self.obj

    def get_slice(self) -> ast.Slice:
        return self.subslice

    def has_slice(self) -> bool:
        return isinstance(self.subslice, ast.Slice)


# lval = rval< obj[slice] >
class IRLoadSubscr(AbstractIRAssign):
    lval: ast.Name
    rval: ast.Subscript
    obj: ast.Name
    subslice: ast.Slice

    def __init__(self, stmt: ast.Assign):
        super().__init__(stmt)
        
        assert isinstance(stmt.targets[0], ast.Name)
        value = stmt.value
        assert isinstance(value, ast.Subscript)
        assert isinstance(value.value, ast.Name)
        assert isinstance(value.slice, ast.Slice)        
        self.obj = value.value
        self.slice = value.slice
    
    def get_lval(self) -> ast.Name:
        return self.lval

    def get_obj(self) -> ast.expr:
        return self.obj

    def get_slice(self) -> ast.expr:
        return self.slice

    def has_slice(self) -> bool:
        return isinstance(self.slice, ast.Slice)


# lval = Phi( items )
class IRPhi(AbstractIRAssign):
    lval: ast.Name
    items: List[Optional[ast.Name]]
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, lval: ast.Name, items: List[Optional[ast.Name]]):        
        stmt = self._get_phi_expr(lval, items)
        super().__init__(stmt)
        
 
    def get_lval(self) -> ast.Name:
        return self.lval
    
    def get_items(self) -> List[Optional[ast.Name]]:
        return self.items
    
    def __str__(self):
        item_str = ", ".join([f"{item.id}" if item is not None else "None" for item in self.items])
        return f"{self.lval} = Phi({item_str})"

    def _get_phi_expr(self, lval: ast.Name, items: List[Optional[ast.Name]]) -> ast.Assign:
        args = ast.List(elts=[ast.Name(id=item.id) if item is not None else ast.Name(id="None") for item in items], ctx=ast.Load())
        phi_expr = ast.Call(func=ast.Name(id="Phi", ctx=ast.Load()), args=args, keywords=[])
        stmt = ast.Assign(targets=[lval], value=phi_expr)
        ast.set_location(stmt, lval.lineno, lval.col_offset)
        ast.fix_missing_locations(stmt)
        return stmt

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if isinstance(ast.Load(), ctxs):
            self.rval = renamer.visit(self.rval)
            for idx in range(len(self.items)):
                if self.items[idx] is not None:
                    self.items[idx] = renamer.visit(self.items[idx])
        if isinstance(ast.Store(), ctxs):
            self.lval = renamer.visit(self.lval)


class IRScope(ABC):
    qualname: str

    @abstractmethod
    def __init__(self, qualname: str):
        self.qualname = qualname

    def get_qualname(self) -> str:
        return self.qualname


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
    
    def get_filename(self) -> str:
        return self.filename

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
        mod_ast = ast.parse(content, filename)
        mod = cls(mod_ast, name=name, filename=filename)
        return mod


class IRFunc(IRScope, IRStatement):
    name: str
    args: ast.arguments
    decorator_list: List[ast.expr]
    returns: ast.expr
    type_comment: str
    stmt: ast.stmt
    is_async: bool
    is_getter: bool
    is_setter: bool
    is_static_method: bool
    is_class_method: bool
    is_instance_method: bool

    cell_vars: Set[str]

    def __init__(self, qualname: str, fn: ast.stmt, cell_vars=None, is_method: bool = False):
        super().__init__(qualname)
        self.name = fn.name
        self.args = fn.args
        self.decorator_list = fn.decorator_list
        self.is_static_method = self.is_class_method = self.is_setter = self.is_getter = False
        for decr in fn.decorator_list:
            if isinstance(decr, ast.Name):
                if decr.id == 'staticmethod':
                    self.is_static_method = True
                elif decr.id == 'classmethod':
                    self.is_class_method = True
                elif decr.id == 'property':
                    self.is_getter = True
            elif isinstance(decr, ast.Attribute):
                if decr.attr == 'setter':
                    self.is_setter = True
        self.is_instance_method = (is_method and not self.is_static_method and not self.is_class_method)
        self.returns = fn.returns
        self.type_comment = fn.type_comment
        self.stmt = fn
        self.is_async = isinstance(fn, ast.AsyncFunctionDef)
        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def get_ast(self) -> ast.stmt:
        return self.stmt

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def get_name(self) -> str:
        if self.is_async:
            return f'<async function {self.name}>'
        else:
            return f'<function {self.name}'

    def get_arg_names(self) -> ast.arguments:
        return self.args

    def __repr__(self) -> str:
        decrs = ', '.join([ast.unparse(decr) for decr in self.decorator_list])
        args = ast.unparse(self.args)
        if self.returns is not None:
            rets = f' -> {ast.unparse(self.returns)}'
        else:
            rets = ''
        if len(decrs) > 0:
            fn_repr = f'fn [{decrs}] {self.name}({args}){rets}'
        else:
            fn_repr = f'fn {self.name}({args}){rets}'
        if self.is_async:
            fn_repr = f'async {fn_repr}'
        return fn_repr

    def __str__(self):
        return self.__repr__()


class IRClass(IRScope, IRStatement):
    name: str
    bases: List[str]
    keywords: List[ast.keyword]
    decorator_list: List[ast.expr]
    ast: ast.ClassDef

    cell_vars: Set[str]

    def __init__(self, qualname: str, cls: ClassDef, cell_vars=None):
        super().__init__(qualname)
        self.name = cls.name
        bases = cls.bases
        assert all(isinstance(i, ast.Name) for i in bases), "Base of the class should be ast.Name"
        self.bases = [base.id for base in cls.bases]
        self.keywords = cls.keywords
        self.decorator_list = cls.decorator_list
        self.ast_repr = cls
        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def get_ast(self) -> ClassDef:
        return self.ast

    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def get_bases(self) -> List[str]:
        return self.bases

    def __repr__(self) -> str:
        decrs = ', '.join([ast.unparse(decr) for decr in self.decorator_list])
        bases = ', '.join(self.bases)
        kws = ', '.join([ast.unparse(kw) for kw in self.keywords])
        if len(decrs) > 0:
            cls_repr = f'class [{decrs}] {self.name}'
        else:
            cls_repr = f'class {self.name}'
        if len(bases) > 0:
            if len(kws) > 0:
                cls_repr = f'{cls_repr}({bases}, {kws})'
            else:
                cls_repr = f'{cls_repr}({bases})'
        elif len(kws) > 0:
            cls_repr = f'{cls_repr}({kws})'
        return cls_repr

    def __str__(self) -> str:
        return self.__repr__()

    def get_name(self) -> str:
        return f'<class {self.name}>'
