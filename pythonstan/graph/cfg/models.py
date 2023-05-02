from typing import *
from abc import ABC, abstractmethod
import ast
from ast import stmt as Statement
from ..graph import Edge, Node, Graph

from pythonstan.utils.var_collector import VarCollector


class BaseBlock(Node):
    idx: int
    stmts: List[Statement]
    store_collector: VarCollector
    load_collector: VarCollector
    del_collector: VarCollector
    cfg: Optional['ControlFlowGraph']

    def __init__(self, idx=-1, cfg=None, stmts=[]):
        self.idx = idx
        self.stmts = [x for x in stmts]
        self.cfg = cfg
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")
        self.del_collector = VarCollector("del")
        for stmt in stmts:
            self.store_collector.visit(stmt)
            self.load_collector.visit(stmt)
            self.del_collector.visit(stmt)

    def set_graph(self, cfg: 'ControlFlowGraph'):
        self.cfg = cfg

    def get_stmts(self):
        return self.stmts

    def get_graph(self):
        return self.cfg

    def get_idx(self):
        return self.idx

    def get_name(self) -> str:
        if self.n_stmt() > 0:
            start = self.stmts[0]
            return f"[{self.idx}] {start.lineno}:{start.col_offset}"
        else:
            return f"[{self.idx}] ?:?"

    def add(self, stmt: Statement):
        ast.fix_missing_locations(stmt)
        self.stmts.append(stmt)
        self.store_collector.visit(stmt)
        self.load_collector.visit(stmt)
        self.del_collector.visit(stmt)
        return self

    def n_stmt(self) -> int:
        return len(self.stmts)

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_dels(self) -> Set[str]:
        return self.del_collector.get_vars()

    def _fix_missing_locations(self):
        for stmt in self.stmts:
            ast.fix_missing_locations(stmt)

    def __str__(self):
        head = self.get_name()
        stmts_str = '\\n'.join([ast.unparse(stmt) for stmt in self.stmts])
        return '\\n'.join([head, stmts_str])


class CFGEdge(Edge):
    src: BaseBlock
    tgt: BaseBlock

    @abstractmethod
    def __init__(self, src, tgt):
        self.src = src
        self.tgt = tgt

    def set_src(self, node: BaseBlock):
        self.src = node

    def set_tgt(self, node: BaseBlock):
        self.tgt = node

    def get_src(self) -> Node:
        return self.src

    def get_tgt(self) -> Node:
        return self.tgt

    def get_name(self) -> str:
        return ""


class NormalEdge(CFGEdge):
    def __init__(self, src, tgt):
        super().__init__(src, tgt)


class IfEdge(CFGEdge):
    test: bool

    def __init__(self, src, tgt, test):
        super().__init__(src, tgt)
        self.test = test

    def get_name(self) -> str:
        return "if_" + str(self.test)


class CallEdge(CFGEdge):
    def __init__(self, src, tgt):
        super().__init__(src, tgt)

    def get_name(self) -> str:
        return 'call'


class ForEdge(CFGEdge):
    def __init__(self, src, tgt):
        super().__init__(src, tgt)

    def get_name(self) -> str:
        return "for"


class ForElseEdge(CFGEdge):
    def __init__(self, src, tgt):
        super().__init__(src, tgt)

    def get_name(self) -> str:
        return "for_else"


class WhileEdge(CFGEdge):
    def __init__(self, start, end):
        super().__init__(start, end)

    def get_name(self) -> str:
        return "while"


class WhileElseEdge(CFGEdge):
    def __init__(self, start, end):
        super().__init__(start, end)

    def get_name(self) -> str:
        return "while_else"


class WithEdge(CFGEdge):
    def __init__(self, start, end, var):
        super().__init__(start, end)
        self.var = var

    def get_name(self) -> str:
        return "with"


class WithEndEdge(CFGEdge):
    def __init__(self, start, end, var):
        super().__init__(start, end)
        self.var = var

    def get_name(self) -> str:
        return "with_end"


class ExceptionEdge(CFGEdge):
    def __init__(self, start, end, e):
        super().__init__(start, end)
        self.e = e

    def get_name(self) -> str:
        return ast.unparse(self.e)


class ExceptionEndEdge(CFGEdge):
    def __init__(self, start, end, e):
        super().__init__(start, end)
        self.e = e

    def get_name(self) -> str:
        return "end: " + ast.unparse(self.e)


class FinallyEdge(CFGEdge):
    def __init__(self, start, end, stmt):
        super().__init__(start, end)
        self.stmt = stmt

    def get_name(self) -> str:
        return "finally"


class FinallyEndEdge(CFGEdge):
    def __init__(self, start, end, stmt):
        super().__init__(start, end)
        self.stmt = stmt

    def get_name(self) -> str:
        return "finally_end"


class ClassDefEdge(CFGEdge):
    cls: 'CFGClass'

    def __init__(self, start, end, class_cfg):
        super().__init__(start, end)
        self.class_cfg = class_cfg

    def get_name(self) -> str:
        return "class"


class ClassEndEdge(CFGEdge):
    cls: 'CFGClass'

    def __init__(self, start, end, cls):
        super().__init__(start, end)
        self.cls = cls

    def get_name(self) -> str:
        return "end: class"


class CFGImport:
    stmt: Union[ast.Import, ast.ImportFrom]

    def __init__(self, stmt):
        self.stmt = stmt

    def __str__(self):
        ast.unparse(self.stmt)


class CFGClassDef:
    name: str
    bases: List[ast.expr]
    keywords: List[ast.keyword]
    decorator_list: List[ast.expr]
    ast_repr: ast.ClassDef

    cell_vars: Set[str]

    def __init__(self, cls: ast.ClassDef, cell_vars={*()}):
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

        self.cell_vars = cell_vars

    def to_ast(self) -> ast.ClassDef:
        return self.ast_repr

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.append(cell_var)

    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n"
        return cell_comment + ast.unparse(self.to_ast())


class CFGFuncDef:
    name: str
    args: ast.arguments
    decorator_list: List[ast.expr]
    returns: ast.expr
    type_comment: str
    ast_repr: ast.FunctionDef

    cell_vars: Set[str]

    def __init__(self, fn: ast.FunctionDef, cell_vars=None):
        if cell_vars is None:
            cell_vars = {*()}
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

        self.cell_vars = cell_vars

    def to_ast(self) -> ast.FunctionDef:
        return self.ast_repr

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n"
        return cell_comment + ast.unparse(self.to_ast())


class CFGAsyncFuncDef(CFGFuncDef):
    ast_repr: ast.AsyncFunctionDef

    def __init__(self, fn: ast.AsyncFunctionDef, cell_vars=None):
        if cell_vars is None:
            cell_vars = {*()}
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

        self.cell_vars = cell_vars


class CFGScope(ABC):
    funcs: List['CFGFunc']
    classes: List['CFGClass']
    imports: List[CFGImport]
    cfg: Optional['ControlFlowGraph']

    @abstractmethod
    def __init__(self, cfg, funcs, classes, imports):
        self.set_cfg(cfg)
        self.set_funcs(funcs)
        self.set_classes(classes)
        self.set_imports(imports)

    def set_funcs(self, funcs: List['CFGFunc']):
        self.funcs = funcs

    def set_classes(self, classes: List['CFGClass']):
        self.classes = classes

    def set_imports(self, imports: List['CFGImport']):
        self.imports = imports

    def set_cfg(self, cfg: 'ControlFlowGraph'):
        self.cfg = cfg

    def add_func(self, func: 'CFGFunc'):
        self.funcs.append(func)

    def add_class(self, cls: 'CFGClass'):
        self.classes.append(cls)

    def add_import(self, imp: 'CFGImport'):
        self.imports.append(imp)

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError


class CFGClass(CFGScope):
    class_def: CFGClassDef
    scope: Optional[CFGScope]

    def __init__(self, class_def, cfg=None, scope=None,
                 funcs=[], classes=[], imports=[]):
        super().__init__(cfg, funcs, classes, imports)
        self.class_def = class_def
        self.scope = scope

    def set_scope(self, scope):
        self.scope = scope

    def get_name(self) -> str:
        return f'cls${self.class_def.name}'

    def __repr__(self) -> str:
        return str(self.class_def)


class CFGFunc(CFGScope):
    func_def: CFGFuncDef
    scope: Optional[CFGScope]

    def __init__(self, func_def, cfg=None, scope=None,
                 funcs=[], classes=[], imports=[]):
        super().__init__(cfg, funcs, classes, imports)
        self.func_def = func_def
        self.scope = scope

    def set_scope(self, scope):
        self.scope = scope

    def get_name(self) -> str:
        return f'fn${self.func_def.name}'

    def __repr__(self) -> str:
        return ast.unparse(self.func_def.to_ast())


class CFGModule(CFGScope):
    def __init__(self, cfg=None, funcs=[], classes=[], imports=[]):
        super().__init__(cfg, funcs, classes, imports)

    def get_name(self) -> str:
        return 'mod'

    def __str__(self):
        return '\n'.join([str(self.cfg), '\n\n'.join([str(c) for c in self.classes]), str(self.funcs)])


# TODO: idx should be maintained by CFG rather than CFG builder
class ControlFlowGraph(Graph):
    entry_blk: BaseBlock
    exit_blks: Set[BaseBlock]
    super_exit_blk: Optional[BaseBlock]
    scope: Optional[CFGScope]
    in_edges: Dict[BaseBlock, List[Edge]]
    out_edges: Dict[BaseBlock, List[Edge]]
    blks: Set[BaseBlock]
    stmts: Set[Statement]
    var_collector: VarCollector

    def __init__(self, entry_blk=None, scope=None):
        self.blks = {*()}
        self.stmts = {*()}
        self.scope = scope
        self.in_edges = {}
        self.out_edges = {}
        self.entry_blk = entry_blk if entry_blk is not None else BaseBlock(0, self)
        if entry_blk is not None:
            self.entry_blk = entry_blk
        else:
            self.entry_blk = BaseBlock(idx=0, cfg=self)
        self.exit_blks = {*()}
        self.super_exit_blk = None
        self.var_collector = VarCollector()

    def preds_of(self, node: Node):
        if isinstance(node, BaseBlock):
            return [e.get_src() for e in self.in_edges_of(node)]
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def succs_of(self, node: Node):
        if isinstance(node, BaseBlock):
            return [e.get_tgt() for e in self.out_edges_of(node)]
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def in_edges_of(self, node: Node):
        if isinstance(node, BaseBlock):
            return self.in_edges[node]
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def out_edges_of(self, node: Node):
        if isinstance(node, BaseBlock):
            return self.out_edges[node]
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def in_degree_of(self, node: Node) -> int:
        if isinstance(node, BaseBlock):
            return len(self.in_edges_of(node))
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def out_degree_of(self, node: Node) -> int:
        if isinstance(node, BaseBlock):
            return len(self.out_edges_of(node))
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def get_entry(self) -> BaseBlock:
        return self.entry_blk

    def get_exit(self) -> BaseBlock:
        if self.super_exit_blk is None:
            raise ValueError(
                "Super Exit Block in current CFG does not exists!")
        return self.super_exit_blk

    def add_node(self, node: Node):
        if isinstance(node, BaseBlock):
            self.add_blk(node)
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def add_blk(self, blk: BaseBlock):
        if blk not in self.blks:
            self.blks.add(blk)
            for stmt in blk.stmts:
                self.stmts.add(stmt)
            self.in_edges[blk] = []
            self.out_edges[blk] = []

    def add_stmt(self, blk: BaseBlock, stmt: Statement):
        blk.add(stmt)
        self.stmts.add(stmt)
        self.var_collector.visit(stmt)

    def add_edge(self, edge: Edge):
        src = edge.get_src()
        tgt = edge.get_tgt()
        if isinstance(src, BaseBlock) and isinstance(tgt, BaseBlock):
            if src not in self.blks:
                self.add_node(src)
            if tgt not in self.blks:
                self.add_node(tgt)
            self.in_edges[tgt].append(edge)
            self.out_edges[src].append(edge)
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def set_scope(self, scope: CFGScope):
        self.scope = scope

    def add_exit(self, blk: BaseBlock):
        self.exit_blks.add(blk)

    def find_var(self, var):
        return self.var_collector.find(var)

    def get_var_map(self):
        return self.var_collector.get_vars()

    def get_var_num(self):
        return self.var_collector.size()

    def add_super_exit_blk(self, blk):
        self.delete_invalid_blk()
        for cur_blk in self.exit_blks:
            if self.out_degree_of(cur_blk) == 0:
                self.exit_blks.add(cur_blk)
        self.add_blk(blk)
        self.super_exit_blk = blk
        for exit_blk in self.exit_blks:
            pass
            self.add_edge(NormalEdge(exit_blk, blk))

    def delete_node(self, node: Node):
        if isinstance(node, BaseBlock):
            self.delete_block(node)
        else:
            raise ValueError(
                "The type of Node in the current CFG can only be BaseBlock!")

    def delete_block(self, blk: BaseBlock):
        if blk in self.exit_blks:
            self.exit_blks.remove(blk)
        for e in self.out_edges_of(blk):
            self.delete_edge(e)
        for e in self.in_edges_of(blk):
            self.delete_edge(e)
        self.in_edges.pop(blk)
        self.out_edges.pop(blk)
        for stmt in blk.stmts:
            self.stmts.remove(stmt)
        self.blks.remove(blk)

    def delete_edge(self, e: Edge):
        self.out_edges_of(e.get_src()).remove(e)
        self.in_edges_of(e.get_tgt()).remove(e)

    def delete_invalid_blk(self):
        q = {blk for blk in self.blks
             if blk != self.entry_blk and self.in_degree_of(blk) == 0}
        while len(q) > 0:
            cur = q.pop()
            out_list = self.succs_of(cur)
            self.delete_block(cur)
            for blk in out_list:
                if self.in_degree_of(blk) == 0:
                    q.add(blk)

    def get_nodes(self) -> Set[BaseBlock]:
        return self.blks
