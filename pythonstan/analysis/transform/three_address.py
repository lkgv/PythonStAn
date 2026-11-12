from __future__ import annotations

import ast
import copy
from ast import Load, NodeTransformer, Store
from collections.abc import Iterable
from typing import TypeGuard, override

from pythonstan.compat.host_iteration import iter_objects
from pythonstan.ir import IRScope
from pythonstan.ir.semantic_residuals import attach_frontend_residual
from pythonstan.utils import TempVarGenerator, destructable, update_ctx
from pythonstan.world.world import World

from ..analysis import AnalysisConfig
from .transform import Transform

__all__ = ["ThreeAddress", "ThreeAddressTransformer", "make_constant"]
FUNC_TEMPLATE = "$func_%d"
CONST_TEMPLATE = "$const_%d"
VAR_TEMPLATE = "$tmp_%d"


def _statement_list(value: object) -> TypeGuard[list[ast.stmt]]:
    return isinstance(value, list) and all(
        isinstance(item, ast.stmt) for item in list.__iter__(value)
    )


def _with_extra_context[NodeT: ast.AST](node: NodeT, ctx: ast.expr_context) -> NodeT:
    ast.AST.__setattr__(node, "ctx", ctx)
    return node


def make_constant(value: object) -> ast.Constant:
    node = ast.Constant(value=None)
    ast.AST.__setattr__(node, "value", value)
    return node


class ThreeAddress(Transform):
    transformer: ThreeAddressTransformer

    def __init__(self, config: AnalysisConfig) -> None:
        super().__init__(config)
        self.transformer = ThreeAddressTransformer()

    @override
    def transform(self, module: IRScope) -> None:
        tac = module.get_ast()
        for _ in range(1):
            tac = self.transformer.visit(tac)
        # three_address_form = self.transformer.visit(module.ast)
        World().scope_manager.set_ir(module, "three address form", tac)


class ConstCollector:
    next_idx: int
    consts: dict[tuple[type[object], object], int]
    values: dict[int, object]

    @staticmethod
    def to_name(idx: int) -> str:
        return CONST_TEMPLATE % idx

    def __init__(self, init_idx: int = 0) -> None:
        self.next_idx = init_idx
        self.consts = {}
        self.values = {}

    def load(self, c: object) -> ast.Name:
        # Python mapping equality conflates True, 1, and 1.0; constants retain
        # their runtime types, and floating point signed zero retains its bits.
        import struct

        identity = (
            struct.pack("!d", c)
            if type(c) is float
            else (struct.pack("!dd", c.real, c.imag) if type(c) is complex else c)
        )
        key = (type(c), identity)
        if key in self.consts:
            idx = self.consts[key]
        else:
            c_idx = self.next_idx
            self.next_idx += 1
            self.consts[key] = c_idx
            self.values[c_idx] = c
            idx = c_idx
        return ast.Name(id=self.to_name(idx), ctx=ast.Load())

    def dump(self) -> list[tuple[str, object]]:
        return [(self.to_name(idx), value) for idx, value in sorted(self.values.items())]


class ThreeAddressTransformer(NodeTransformer):
    tmp_gen: TempVarGenerator
    tmp_func_gen: TempVarGenerator
    const_colle: ConstCollector
    import_stmts: set[ast.stmt]

    def __init__(self) -> None:
        self.reset()

    def _visit_expr(self, node: ast.expr) -> tuple[list[ast.stmt], ast.expr]:
        raw: object = self.visit(node)
        parts = iter_objects(raw)
        try:
            prefix, value = next(parts), next(parts)
        except StopIteration as error:
            raise ValueError("TAC expression visitor must return exactly two values") from error
        try:
            next(parts)
        except StopIteration:
            pass
        else:
            raise ValueError("TAC expression visitor returned too many values")
        if not isinstance(value, ast.expr):
            raise TypeError("TAC expression visitor must return an expression")
        if _statement_list(prefix):
            return prefix, value
        statements: list[ast.stmt] = []
        pending = iter_objects(prefix)
        while True:
            try:
                statement = next(pending)
            except StopIteration:
                break
            if not isinstance(statement, ast.stmt):
                raise TypeError("TAC statement prefix must contain statements")
            statements.append(statement)
        return statements, value

    def _visit_stmt(self, node: ast.stmt) -> list[ast.stmt] | ast.stmt:
        raw: object = self.visit(node)
        if _statement_list(raw) or isinstance(raw, ast.stmt):
            return raw
        raise TypeError("TAC statement visitor must return statements")

    def not_temp(self, exp: ast.expr) -> bool:
        return not (isinstance(exp, ast.Name) and "$" in exp.id)

    def reset(
        self, v_tmpl: str = VAR_TEMPLATE, fn_tmpl: str = FUNC_TEMPLATE, c_tmpl: str = CONST_TEMPLATE
    ) -> None:
        self.tmp_gen = TempVarGenerator(template=v_tmpl)
        self.tmp_func_gen = TempVarGenerator(template=fn_tmpl)
        self.const_colle = ConstCollector()
        self.import_stmts = {*()}

    def resolve_single_Assign(
        self, tgt: ast.expr, value: ast.expr, stmt: ast.AST
    ) -> list[ast.stmt]:
        """
        Converts a Python assignment statement to three-address code form.

        This method handles various assignment patterns including:
        - Simple assignments (x = y)
        - Tuple/list unpacking (a, b = x, y)
        - Starred expressions (a, *b, c = some_list)
        - Complex RHS expressions requiring temporary variables

        Parameters:
        ----------
        tgt : ast.AST
            The target of the assignment (LHS), which can be a Name, Tuple, List, etc.
        value : ast.AST
            The value being assigned (RHS)
        stmt : ast.AST
            The original assignment statement (for source location information)

        Returns:
        -------
        list[ast.stmt]
            A list of AST statements that implement the assignment in three-address form

        Examples:
        --------
        Input:  a = b + c
        Output: $tmp_1 = b + c
                a = $tmp_1

        Input:  a, b = x, y
        Output: a = x
                b = y

        Input:  a, *b, c = some_list
        Output: $tmp_1 = list(some_list)
                a = $tmp_1[0]
                b = $tmp_1[1:-1]
                c = $tmp_1[-1]
        """
        ass_blk: list[ast.stmt]
        unpack_blk: list[ast.stmt]

        def has_star(ls: ast.Tuple | ast.List) -> bool:
            return any(elt for elt in ls.elts if isinstance(elt, ast.Starred))

        tblk, texp = self._visit_expr(tgt)
        ass_blk = []
        if isinstance(texp, ast.Name):
            ins = ast.Assign(targets=[texp], value=value)
            ast.copy_location(ins, stmt)
            ass_blk.append(ins)
        # unpacking cannot be desugarred in semantic level (because of the existence of iterator)
        # only the situation that both sides has equal length can be splitted
        elif (
            destructable(texp)
            and destructable(value)
            and len(texp.elts) == len(value.elts)
            and not has_star(value)
        ):
            for t, v in zip(texp.elts, value.elts, strict=False):
                if isinstance(t, ast.Name):
                    ins = ast.Assign(targets=[t], value=v)
                    ast.copy_location(ins, stmt)
                    ass_blk.append(ins)
                elif isinstance(t, ast.Starred):
                    v = ast.List(elts=[update_ctx(v, Load())], ctx=Load())
                    ins = ast.Assign(targets=[t.value], value=v)
                    ast.copy_location(ins, stmt)
                    ass_blk.append(ins)
                else:
                    tmp_l, tmp_s = self.tmp_gen()
                    ins1 = ast.Assign(targets=[tmp_s], value=v)
                    ast.copy_location(ins1, stmt)
                    ins2 = ast.Assign(targets=[t], value=tmp_l)
                    ast.copy_location(ins2, stmt)
                    ass_blk.extend([ins1, ins2])
        elif destructable(texp):
            if not isinstance(value, ast.Name):
                v_blk, v_elt = self.split_expr(value)
                ass_blk.extend(v_blk)
                value = v_elt
            tmp_l, tmp_s = self.tmp_gen()
            ins1 = ast.Assign(
                targets=[tmp_s],
                value=ast.Call(func=ast.Name(id="list", ctx=Load()), args=[value], keywords=[]),
            )
            ast.copy_location(ins1, stmt)
            ass_blk.append(ins1)
            unpack_blk = []
            if has_star(texp):
                l_idx, r_idx = 0, 1
                t_star = None
                for t in texp.elts:
                    if isinstance(t, ast.Starred):
                        break
                    ins = ast.Assign(
                        targets=[t],
                        value=ast.Subscript(
                            value=tmp_l, slice=self.const_colle.load(l_idx), ctx=ast.Load()
                        ),
                    )
                    ast.copy_location(ins, stmt)
                    unpack_blk.append(ins)
                    l_idx += 1
                for t in texp.elts[::-1]:
                    if isinstance(t, ast.Starred):
                        t_star = t
                        break
                    ins = ast.Assign(
                        targets=[t],
                        value=ast.Subscript(
                            value=tmp_l,
                            slice=ast.UnaryOp(op=ast.USub(), operand=self.const_colle.load(r_idx)),
                            ctx=ast.Load(),
                        ),
                    )
                    ast.copy_location(ins, stmt)
                    unpack_blk.append(ins)
                    r_idx += 1
                if t_star is not None:
                    lower_idx = self.const_colle.load(l_idx)
                    if r_idx == 1:
                        upper_idx = None
                    else:
                        upper_idx = ast.UnaryOp(
                            op=ast.USub(), operand=self.const_colle.load(r_idx - 1)
                        )
                    slc = ast.Slice(lower=lower_idx, upper=upper_idx)
                    ast.AST.__setattr__(slc, "ctx", Load())
                    ins = ast.Assign(
                        targets=[t_star.value],
                        value=ast.Subscript(value=tmp_l, slice=slc, ctx=Load()),
                    )
                    ast.copy_location(ins, stmt)
                    unpack_blk.append(ins)
            else:
                for idx, t in enumerate(texp.elts):
                    ins = ast.Assign(
                        targets=[t],
                        value=ast.Subscript(
                            value=tmp_l, slice=self.const_colle.load(idx), ctx=ast.Load()
                        ),
                    )
                    ast.copy_location(ins, stmt)
                    unpack_blk.append(ins)
            ass_blk.extend(self.visit_stmt_list(unpack_blk))
        else:
            tmp_l, tmp_s = self.tmp_gen()
            ins1 = ast.Assign(targets=[tmp_s], value=value)
            ast.copy_location(ins1, stmt)
            ins2 = ast.Assign(targets=[texp], value=tmp_l)
            ast.copy_location(ins2, stmt)
            ass_blk.extend((ins1, ins2))
        return ass_blk + tblk

    def split_expr(self, exp: ast.expr) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, e = self._visit_expr(exp)
        if not isinstance(e, (ast.Name, ast.Starred)):
            tmp_l, tmp_s = self.tmp_gen()
            if hasattr(exp, "ctx") and isinstance(exp.ctx, Store):
                # tmp = do(...); e = tmp; visit(exp)=e
                ins = ast.Assign(targets=[e], value=tmp_l)
                ast.copy_location(ins, exp)
                if isinstance(e, (ast.Tuple, ast.List)):
                    blk.insert(0, ins)  # assign after unpack
                else:
                    blk.append(ins)  # load object and then assign
                e = tmp_s
            else:
                # e=visit(exp); tmp=e; do(..., tmp, ...)
                ins = ast.Assign(targets=[tmp_s], value=e)
                ast.copy_location(ins, exp)
                blk.append(ins)
                e = tmp_l
        return self.visit_stmt_list(blk), e

    @override
    def visit_BinOp(self, node: ast.BinOp) -> tuple[list[ast.stmt], ast.expr]:
        lblk, lval = self.split_expr(node.left)
        rblk, rval = self.split_expr(node.right)
        ins = ast.BinOp(left=lval, op=node.op, right=rval)
        ast.copy_location(ins, node)
        return lblk + rblk, ins

    @override
    def visit_UnaryOp(self, node: ast.UnaryOp) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        if isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            operand: object = node.operand.value
            if not isinstance(operand, (int, float, complex)):
                raise TypeError("bad operand type for unary -")
            value = self.const_colle.load(-operand)
            ast.copy_location(value, node)
            return [], value
        blk, val = self.split_expr(node.operand)
        ins = ast.UnaryOp(operand=val, op=node.op)
        ast.copy_location(ins, node)
        return blk, ins

    @override
    def visit_IfExp(self, node: ast.IfExp) -> tuple[list[ast.stmt], ast.expr]:
        t_blk, t_val = self.split_expr(node.test)
        b_blk, b_val = self.split_expr(node.body)
        o_blk, o_val = self.split_expr(node.orelse)
        tmp_l, tmp_s = self.tmp_gen()
        b_ass = ast.Assign(targets=[tmp_s], value=b_val)
        ast.copy_location(b_ass, node.body)
        b_blk.append(b_ass)
        o_ass = ast.Assign(targets=[tmp_s], value=o_val)
        ast.copy_location(o_ass, node.orelse)
        o_blk.append(o_ass)
        ins = ast.If(test=t_val, body=b_blk, orelse=o_blk)
        ast.copy_location(ins, node)
        t_blk.append(ins)
        return t_blk, tmp_l

    @override
    def visit_Lambda(self, node: ast.Lambda) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        (f_load,) = self.tmp_func_gen(ctxs=[Load()])
        f_name = f_load.id
        blk = []
        new_func = ast.FunctionDef(
            name=f_name,
            args=node.args,
            body=[ast.Return(value=node.body)],
            returns=None,
            type_comment=None,
            decorator_list=[],
            type_params=[],
        )
        ast.copy_location(new_func, node)
        blk.extend(self.visit_FunctionDef(new_func))
        return blk, f_load

    @override
    def visit_NamedExpr(self, node: ast.NamedExpr) -> tuple[list[ast.stmt], ast.expr]:
        AGGRESSIVE = True
        if AGGRESSIVE:
            v_blk, v_elt = self._visit_expr(node.value)
            t_blk, t_elt = self._visit_expr(node.target)
            ins = ast.Assign(targets=[t_elt], value=v_elt)
            ast.copy_location(ins, node)
            return v_blk + t_blk + [ins], v_elt
        v_blk, v_elt = self.split_expr(node.value)
        t_blk, t_elt = self._visit_expr(node.target)
        if not isinstance(t_elt, ast.Name):
            raise TypeError("Named expression target must be a name")
        expression = ast.NamedExpr(target=t_elt, value=v_elt)
        ast.copy_location(expression, node)
        return v_blk + t_blk, expression

    @override
    def visit_Tuple(self, node: ast.Tuple) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        elts: list[ast.expr]
        blk, elts = [], []
        for old_elt in node.elts:
            tmp_blk, tmp_elt = self.split_expr(old_elt)
            blk.extend(tmp_blk)
            elts.append(tmp_elt)
        exp = ast.Tuple(elts=elts, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_List(self, node: ast.List) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        elts: list[ast.expr]
        blk, elts = [], []
        for old_elt in node.elts:
            tmp_blk, tmp_elt = self.split_expr(old_elt)
            blk.extend(tmp_blk)
            elts.append(tmp_elt)
        exp = ast.List(elts=elts, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_Set(self, node: ast.Set) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        elts: list[ast.expr]
        blk, elts = [], []
        for old_elt in node.elts:
            tmp_blk, tmp_elt = self.split_expr(old_elt)
            blk.extend(tmp_blk)
            elts.append(tmp_elt)
        exp = ast.Set(elts=elts)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_Dict(self, node: ast.Dict) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        keys: list[ast.expr | None]
        values: list[ast.expr]
        blk, keys, values = [], [], []
        for k, v in zip(node.keys, node.values, strict=False):
            if k is not None:
                k_blk, k_elt = self.split_expr(k)
                blk.extend(k_blk)
                keys.append(k_elt)
            else:
                keys.append(None)
            v_blk, v_elt = self.split_expr(v)
            blk.extend(v_blk)
            values.append(v_elt)
        exp = ast.Dict(keys=keys, values=values)
        ast.copy_location(exp, node)
        return blk, exp

    def trans_comp(self, comp: ast.comprehension, body: list[ast.stmt]) -> ast.For | ast.AsyncFor:
        for idx in range(len(comp.ifs) - 1, -1, -1):
            cond = comp.ifs[idx]
            ins = ast.If(test=cond, body=body, orelse=[])
            body = [ins]
        if comp.is_async == 0:
            loop: ast.For | ast.AsyncFor = ast.For(
                target=comp.target, iter=comp.iter, body=body, orelse=[]
            )
        else:
            loop = ast.AsyncFor(target=comp.target, iter=comp.iter, body=body, orelse=[])
        return loop

    @override
    def visit_ListComp(self, node: ast.ListComp) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        body: list[ast.stmt]
        list_l, list_s = self.tmp_gen()
        list_init = ast.Assign(targets=[list_s], value=ast.List(elts=[], ctx=Load()))
        ast.copy_location(list_init, node)
        body = [
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=list_l, attr="append", ctx=Load()),
                    args=[node.elt],
                    keywords=[],
                )
            )
        ]
        for idx in range(len(node.generators) - 1, -1, -1):
            comp = node.generators[idx]
            ins = self.trans_comp(comp, body)
            body = [ins]
        blk = self.visit_stmt_list(body)
        for lowered in blk:
            ast.copy_location(lowered, node)
            ast.fix_missing_locations(lowered)
        blk.insert(0, list_init)
        return blk, list_l

    @override
    def visit_SetComp(self, node: ast.SetComp) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        body: list[ast.stmt]
        set_l, set_s = self.tmp_gen()
        set_init = ast.Assign(targets=[set_s], value=_with_extra_context(ast.Set(elts=[]), Load()))
        ast.copy_location(set_init, node)
        body = [
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=set_l, attr="add", ctx=Load()),
                    args=[node.elt],
                    keywords=[],
                )
            )
        ]
        for idx in range(len(node.generators) - 1, -1, -1):
            comp = node.generators[idx]
            ins = self.trans_comp(comp, body)
            body = [ins]
        blk = self.visit_stmt_list(body)
        for lowered in blk:
            ast.copy_location(lowered, node)
            ast.fix_missing_locations(lowered)
        blk.insert(0, set_init)
        return blk, set_l

    @override
    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        body: list[ast.stmt]
        (fn_name,) = self.tmp_func_gen(ctxs=[Load()])

        body = [ast.Expr(value=ast.Yield(value=node.elt))]
        for idx in range(len(node.generators) - 1, -1, -1):
            comp = node.generators[idx]
            ins = self.trans_comp(comp, body)
            body = [ins]
        blk = self.visit_stmt_list(body)
        for lowered in blk:
            ast.copy_location(lowered, node)
            ast.fix_missing_locations(lowered)
        fn = ast.FunctionDef(
            name=fn_name.id,
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=blk,
            decorator_list=[],
            returns=None,
            type_comment=None,
            type_params=[],
        )
        call_elt = ast.Call(func=fn_name, args=[], keywords=[])
        ast.copy_location(call_elt, node)
        return [fn], call_elt

    @override
    def visit_DictComp(self, node: ast.DictComp) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        body: list[ast.stmt]
        dict_l, dict_s = self.tmp_gen()
        dict_init = ast.Assign(targets=[dict_s], value=ast.Dict(keys=[], values=[]))
        ast.copy_location(dict_init, node)
        body = [
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=dict_l, attr="setdefault", ctx=Load()),
                    args=[node.key, node.value],
                    keywords=[],
                )
            )
        ]
        for idx in range(len(node.generators) - 1, -1, -1):
            comp = node.generators[idx]
            ins = self.trans_comp(comp, body)
            body = [ins]
        blk = self.visit_stmt_list(body)
        for lowered in blk:
            ast.copy_location(lowered, node)
            ast.fix_missing_locations(lowered)
        blk.insert(0, dict_init)
        return blk, dict_l

    @override
    def visit_Await(self, node: ast.Await) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, elt = self._visit_expr(node.value)
        tmp_l, tmp_s = self.tmp_gen()
        ins = ast.Assign(targets=[tmp_s], value=elt)
        ast.copy_location(ins, node)
        blk.append(ins)
        exp = ast.Await(value=tmp_l)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_Yield(self, node: ast.Yield) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, elt = [], None
        if node.value is not None:
            blk, elt = self._visit_expr(node.value)
            tmp_l, tmp_s = self.tmp_gen()
            ins = ast.Assign(targets=[tmp_s], value=elt)
            ast.copy_location(ins, node)
            blk.append(ins)
            exp = ast.Yield(value=tmp_l)
            ast.copy_location(exp, node)
        else:
            blk, exp = [], node
        return blk, exp

    @override
    def visit_YieldFrom(self, node: ast.YieldFrom) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, elt = self._visit_expr(node.value)
        tmp_l, tmp_s = self.tmp_gen()
        ins = ast.Assign(targets=[tmp_s], value=elt)
        ast.copy_location(ins, node)
        blk.append(ins)
        exp = ast.YieldFrom(value=tmp_l)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_BoolOp(self, node: ast.BoolOp) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt] | None
        elts: list[ast.expr]
        blk, elts = None, []
        test_list = [self._visit_expr(val) for val in node.values]
        tmp_l, tmp_s = self.tmp_gen()
        for tmp_blk, tmp_elt in test_list[::-1]:
            ass_ins = ast.Assign(targets=[tmp_s], value=tmp_elt)
            ast.copy_location(ass_ins, node)
            tmp_blk.append(ass_ins)
            if blk is not None:
                if isinstance(node.op, ast.And):
                    tmp_test = tmp_l
                elif isinstance(node.op, ast.Or):
                    # Generated guards also obey the TAC Name invariant.
                    test_load, test_store = self.tmp_gen()
                    test_assignment = ast.Assign(
                        targets=[test_store], value=ast.UnaryOp(op=ast.Not(), operand=tmp_l)
                    )
                    ast.copy_location(test_assignment, node)
                    tmp_blk.append(test_assignment)
                    tmp_test = test_load
                else:
                    raise NotImplementedError("The op is not a bool op!")
                tmp_blk.append(ast.If(test=tmp_test, body=blk, orelse=[]))
            blk = tmp_blk
            elts.insert(0, tmp_elt)
        return ([] if blk is None else blk), tmp_l

    @override
    def visit_Compare(self, node: ast.Compare) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt] | None
        elts: list[ast.expr]
        values: list[ast.expr]
        blk, elts = None, []
        values = [node.left] + node.comparators
        test_list = [self.split_expr(val) for val in values]
        tmp_l, tmp_s = self.tmp_gen()
        for i in range(len(test_list) - 1, -1, -1):
            tmp_blk, tmp_elt = test_list[i]
            if i > 0:
                _, lhs_elt = test_list[i - 1]
                tmp_test = ast.Compare(left=lhs_elt, ops=[node.ops[i - 1]], comparators=[tmp_elt])
                ass_ins = ast.Assign(targets=[tmp_s], value=tmp_test)
                ast.copy_location(ass_ins, node)
                tmp_blk.append(ass_ins)
                if blk is not None:
                    tmp_blk.append(ast.If(test=tmp_l, body=blk, orelse=[]))
            else:
                if blk is None:
                    raise TypeError("Comparison has no right operand")
                tmp_blk.extend(blk)
            blk = tmp_blk
            elts.insert(0, tmp_elt)
        return ([] if blk is None else blk), tmp_l

    @override
    def visit_Call(self, node: ast.Call) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, args, keywords = [], [], []
        func_blk, func_elt = self._visit_expr(node.func)
        if not (isinstance(func_elt, ast.Name)):
            # or isinstance(func_elt, ast.Attribute)):
            func_blk, func_elt = self.split_expr(node.func)
        blk.extend(func_blk)
        for arg in node.args:
            arg_blk, arg_elt = self.split_expr(arg)
            blk.extend(arg_blk)
            args.append(arg_elt)
        for kw in node.keywords:
            v_blk, v_elt = self.split_expr(kw.value)
            new_kw = ast.keyword(arg=kw.arg, value=v_elt)
            blk.extend(v_blk)
            keywords.append(new_kw)
        tmp_l, tmp_s = self.tmp_gen()
        call = ast.Call(func=func_elt, args=args, keywords=keywords)
        # Generated assignments can be revisited by split_expr. Preserve the
        # call's own source span so that a second visit cannot inherit line 1.
        ast.copy_location(call, node)
        ins = ast.Assign(targets=[tmp_s], value=call)
        ast.copy_location(ins, node)
        blk.append(ins)
        return ([] if blk is None else blk), tmp_l

    @override
    def visit_FormattedValue(self, node: ast.FormattedValue) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, v = self.split_expr(node.value)
        fv = ast.FormattedValue(value=v, conversion=node.conversion, format_spec=node.format_spec)
        ast.copy_location(fv, node)
        return blk, fv

    @override
    def visit_JoinedStr(self, node: ast.JoinedStr) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        values: list[ast.expr]
        blk, values = [], []
        for v in node.values:
            if isinstance(v, ast.Constant):
                tmp_blk, tmp_v = self._visit_expr(v)
                fvalue = ast.FormattedValue(value=tmp_v, conversion=-1)
                ast.copy_location(fvalue, v)
                values.append(fvalue)
                blk.extend(tmp_blk)
            elif isinstance(v, ast.FormattedValue):
                tmp_blk, tmp_v = self._visit_expr(v.value)
                fvalue = ast.FormattedValue(value=tmp_v, conversion=v.conversion)
                ast.copy_location(fvalue, v)
                values.append(fvalue)
                blk.extend(tmp_blk)
        jstr = ast.JoinedStr(values=values)
        ast.copy_location(jstr, node)
        return blk, jstr

    @override
    def visit_Name(self, node: ast.Name) -> tuple[list[ast.stmt], ast.expr]:
        return [], node

    @override
    def visit_Constant(self, node: ast.Constant) -> tuple[list[ast.stmt], ast.expr]:
        return [], self.const_colle.load(node.value)

    @override
    def visit_Starred(self, node: ast.Starred) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, elt = self.split_expr(node.value)
        exp = ast.Starred(value=elt, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_Attribute(self, node: ast.Attribute) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk, elt = self.split_expr(node.value)
        exp = ast.Attribute(value=elt, attr=node.attr, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_Subscript(self, node: ast.Subscript) -> tuple[list[ast.stmt], ast.expr]:
        if isinstance(node.slice, ast.Slice):
            s_blk, s_elt = self._visit_expr(node.slice)
        else:
            s_blk, s_elt = self.split_expr(node.slice)
        v_blk, v_elt = self.split_expr(node.value)
        exp = ast.Subscript(value=v_elt, slice=s_elt, ctx=node.ctx)
        ast.copy_location(exp, node)
        return v_blk + s_blk, exp

    @override
    def visit_Slice(self, node: ast.Slice) -> tuple[list[ast.stmt], ast.expr]:
        blk: list[ast.stmt]
        blk = []
        lower, upper, step = None, None, None
        if node.lower is not None:
            l_blk, lower = self.split_expr(node.lower)
            blk.extend(l_blk)
        if node.upper is not None:
            u_blk, upper = self.split_expr(node.upper)
            blk.extend(u_blk)
        if node.step is not None:
            s_blk, step = self.split_expr(node.step)
            blk.extend(s_blk)
        exp = ast.Slice(lower=lower, upper=upper, step=step)
        ast.copy_location(exp, node)
        return blk, exp

    @override
    def visit_Return(self, node: ast.Return) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk, v = [], None
        if node.value is not None:
            blk, v = self.split_expr(node.value)
        stmt = ast.Return(value=v)
        ast.copy_location(stmt, node)
        blk.append(stmt)
        return blk

    @override
    def visit_Delete(self, node: ast.Delete) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        for tgt in node.targets:
            tmp_blk, tmp_elt = self.split_expr(tgt)
            stmt = ast.Delete(targets=[tmp_elt])
            ast.copy_location(stmt, tgt)
            blk.extend(tmp_blk)
            blk.append(stmt)
        return blk

    @override
    def visit_AugAssign(self, node: ast.AugAssign) -> list[ast.stmt]:
        expand_stmt = ast.Assign(
            targets=[node.target],
            value=ast.BinOp(left=update_ctx(node.target, Load()), op=node.op, right=node.value),
        )
        ast.copy_location(expand_stmt, node)
        return self.visit_Assign(expand_stmt)

    @override
    def visit_Assign(self, node: ast.Assign) -> list[ast.stmt]:
        blk: list[ast.stmt]
        tgts = node.targets
        blk, exp = self._visit_expr(node.value)
        for tgt in tgts[::-1]:
            tblk = self.resolve_single_Assign(tgt, exp, node)
            blk.extend(tblk)
        return blk

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> list[ast.stmt]:
        blk: list[ast.stmt]
        if node.value is None:
            if node.simple == 1:
                blk = [node]
            else:
                blk, tmp_t = self.split_expr(node.target)
                if not isinstance(tmp_t, (ast.Name, ast.Attribute, ast.Subscript)):
                    raise TypeError("Annotated target is not assignable")
                ins = ast.AnnAssign(target=tmp_t, annotation=node.annotation, value=None, simple=1)
                ast.copy_location(ins, node)
                blk.insert(0, ins)
        else:
            blk, v_elt = self._visit_expr(node.value)
            if node.simple == 1:
                ins1 = ast.AnnAssign(
                    target=node.target, annotation=node.annotation, value=None, simple=1
                )
                ast.copy_location(ins1, node)
                ins2 = ast.Assign(targets=[node.target], value=v_elt)
                ast.copy_location(ins2, node)
                blk.extend([ins1, ins2])
            else:
                tmp_blk, tmp_t = self.split_expr(node.target)
                if not isinstance(tmp_t, (ast.Name, ast.Attribute, ast.Subscript)):
                    raise TypeError("Annotated target is not assignable")
                ins1 = ast.AnnAssign(target=tmp_t, annotation=node.annotation, value=None, simple=1)
                ast.copy_location(ins1, node)
                ins2 = ast.Assign(targets=[tmp_t], value=v_elt)
                ast.copy_location(ins2, node)
                blk.extend([ins1, ins2])
                blk.extend(tmp_blk)
        return blk

    """
    For(expr target, expr iter, stmt* body, stmt* orelse, string? type_comment)

    Iteration uses a fresh identity sentinel and re-evaluates next() at the
    loop header. None is a valid element, and continue must advance the
    iterator. The shared while normalization also preserves for-else.
    """

    @override
    def visit_For(self, node: ast.For) -> list[ast.stmt]:
        ass_blk: list[ast.stmt]
        body: list[ast.stmt]
        body = []
        iter_blk, iter_val = self.split_expr(node.iter)
        body.extend(iter_blk)
        iter_l, iter_s = self.tmp_gen()
        iter_stmt = ast.Assign(
            targets=[iter_s],
            value=ast.Call(func=ast.Name(id="iter", ctx=Load()), args=[iter_val], keywords=[]),
        )
        ast.copy_location(iter_stmt, node.iter)
        attach_frontend_residual(
            iter_stmt,
            node,
            "frontend_for_builtin_identity_unproved",
            "for lowering uses source-visible iter/next names; builtin identity and "
            "implicit iteration special-method lookup have not been established",
            (
                "implicit_iteration_dispatch",
                "builtin_binding_identity",
                "may_invoke_callbacks",
                "may_mutate_heap",
                "may_raise",
            ),
        )
        body.append(iter_stmt)
        sentinel_l, sentinel_s = self.tmp_gen()
        # An empty list is fresh without depending on a source-visible
        # `object` name that user code can shadow. Only identity is tested.
        sentinel_stmt = ast.Assign(targets=[sentinel_s], value=ast.List(elts=[], ctx=Load()))
        ast.copy_location(sentinel_stmt, node.iter)
        body.append(sentinel_stmt)
        tmp_l, tmp_s = self.tmp_gen()
        next_stmt = ast.Assign(
            targets=[tmp_s],
            value=ast.Call(
                func=ast.Name(id="next", ctx=Load()), args=[iter_l, sentinel_l], keywords=[]
            ),
        )
        ast.copy_location(next_stmt, node.iter)
        tmp_test_l, tmp_test_s = self.tmp_gen()
        test_stmt = ast.Assign(
            targets=[tmp_test_s],
            value=ast.Compare(left=tmp_l, ops=[ast.IsNot()], comparators=[sentinel_l]),
        )
        ast.copy_location(test_stmt, node.target)
        ass_blk = self.resolve_single_Assign(node.target, tmp_l, node.iter)
        b_blk = self.visit_stmt_list(node.body)
        ass_blk.extend(b_blk)
        o_blk = self.visit_stmt_list(node.orelse)
        body.extend(self._normalize_while([next_stmt, test_stmt], tmp_test_l, ass_blk, o_blk, node))
        return body

    @override
    def visit_AsyncFor(self, node: ast.AsyncFor) -> list[ast.stmt]:
        ass_blk: list[ast.stmt]
        body: list[ast.stmt]
        body = []
        iter_blk, iter_val = self.split_expr(node.iter)
        body.extend(iter_blk)
        iter_l, iter_s = self.tmp_gen()
        iter_stmt = ast.Assign(
            targets=[iter_s],
            value=ast.Call(func=ast.Name(id="aiter", ctx=Load()), args=[iter_val], keywords=[]),
        )
        ast.AST.__setattr__(iter_stmt.value, "_pythonstan_async_iteration_operation", "aiter")
        ast.copy_location(iter_stmt, node.iter)
        attach_frontend_residual(
            iter_stmt,
            node,
            "frontend_async_for_protocol_unmodeled",
            "async-for protocol may-flow does not discharge StopAsyncIteration routing, "
            "exhaustion, suspension order or cancellation cleanup",
            (
                "async_iteration_dispatch",
                "await_suspension",
                "exception_routing",
                "cancellation_cleanup",
                "may_invoke_callbacks",
                "may_mutate_heap",
                "may_raise",
            ),
        )
        body.append(iter_stmt)
        tmp_l, tmp_s = self.tmp_gen()
        step_l, step_s = self.tmp_gen()
        next_stmt = ast.Assign(
            targets=[step_s],
            value=ast.Call(func=ast.Name(id="anext", ctx=Load()), args=[iter_l], keywords=[]),
        )
        ast.AST.__setattr__(next_stmt.value, "_pythonstan_async_iteration_operation", "anext")
        ast.copy_location(next_stmt, node.iter)
        await_stmt = ast.Assign(targets=[tmp_s], value=ast.Await(value=step_l))
        ast.copy_location(await_stmt, node.iter)

        body.append(next_stmt)
        body.append(await_stmt)
        tmp_test_l, tmp_test_s = self.tmp_gen()
        test_stmt = ast.Assign(
            targets=[tmp_test_s],
            value=ast.Compare(
                left=tmp_l, ops=[ast.IsNot()], comparators=[self.const_colle.load(None)]
            ),
        )
        ast.copy_location(test_stmt, node.target)
        body.append(test_stmt)
        ass_blk = self.resolve_single_Assign(node.target, tmp_l, node.iter)
        b_blk = self.visit_stmt_list(node.body)
        ass_blk.extend(b_blk)
        ass_blk.append(copy.deepcopy(next_stmt))
        ass_blk.append(copy.deepcopy(await_stmt))
        ass_blk.append(copy.deepcopy(test_stmt))
        o_blk = self.visit_stmt_list(node.orelse)
        while_stmt = ast.While(test=tmp_test_l, body=ass_blk, orelse=o_blk)
        ast.copy_location(while_stmt, node)
        body.append(while_stmt)
        return body

    @override
    def visit_While(self, node: ast.While) -> list[ast.stmt]:
        t_blk, test_elt = self.split_expr(node.test)
        b_blk = self.visit_stmt_list(node.body)
        o_blk = self.visit_stmt_list(node.orelse)
        return self._normalize_while(t_blk, test_elt, b_blk, o_blk, node)

    def _normalize_while(
        self,
        test_prefix: list[ast.stmt],
        test: ast.expr,
        body: list[ast.stmt],
        orelse: list[ast.stmt],
        source: ast.AST,
    ) -> list[ast.stmt]:
        """Keep repeated guard evaluation inside an ordinary executable AST.

        A continue returns to the guard; break skips else. The original else
        stays outside this loop so any break/continue there still targets an
        enclosing loop, as in the source program.
        """
        failure: list[ast.stmt]
        result: list[ast.stmt]
        result, exhausted = [], None
        failure = []
        if orelse:
            exhausted, exhausted_store = self.tmp_gen()
            result.append(
                ast.copy_location(
                    ast.Assign(targets=[exhausted_store], value=ast.Constant(False)), source
                )
            )
            failure.append(
                ast.copy_location(
                    ast.Assign(targets=[copy.deepcopy(exhausted_store)], value=ast.Constant(True)),
                    source,
                )
            )
        failure.append(ast.copy_location(ast.Break(), source))
        guard = ast.copy_location(
            ast.If(test=test, body=body or [ast.Pass()], orelse=failure), source
        )
        loop = ast.copy_location(
            ast.While(
                test=self.const_colle.load(True), body=list(test_prefix) + [guard], orelse=[]
            ),
            source,
        )
        result.append(loop)
        if orelse:
            if exhausted is None:
                raise RuntimeError("Loop else requires exhaustion temporary")
            result.append(ast.copy_location(ast.If(test=exhausted, body=orelse, orelse=[]), source))
        return result

    @override
    def visit_If(self, node: ast.If) -> list[ast.stmt]:
        t_blk, t_val = self.split_expr(node.test)
        b_blk = self.visit_stmt_list(node.body)
        o_blk = self.visit_stmt_list(node.orelse)
        ins = ast.If(test=t_val, body=b_blk, orelse=o_blk)
        ast.copy_location(ins, node)
        t_blk.append(ins)
        return t_blk

    @override
    def visit_With(self, node: ast.With) -> list[ast.stmt]:
        blk: list[ast.stmt]
        items: list[tuple[list[ast.stmt], ast.expr, ast.Name, list[ast.stmt] | None]]
        with_blk: list[ast.stmt] | None
        items = []
        for item in node.items:
            ctx_blk, ctx_e = self.split_expr(item.context_expr)
            tmp_l, tmp_s = self.tmp_gen()
            if item.optional_vars is not None:
                with_blk = self.resolve_single_Assign(item.optional_vars, tmp_l, item)
                items.append((ctx_blk, ctx_e, tmp_s, with_blk))
            else:
                items.append((ctx_blk, ctx_e, tmp_s, None))
        blk = self.visit_stmt_list(node.body)

        for idx in range(len(items) - 1, -1, -1):
            ctx_blk, ctx_e, tmp_s, with_blk = items[idx]
            if with_blk is not None:
                with_blk.extend(blk)
            else:
                with_blk = blk
            with_stmt = ast.With(
                items=[ast.withitem(context_expr=ctx_e, optional_vars=tmp_s)], body=with_blk
            )
            ast.copy_location(with_stmt, node)
            blk = ctx_blk
            blk.append(with_stmt)
        return blk

    @override
    def visit_AsyncWith(self, node: ast.AsyncWith) -> list[ast.stmt]:
        blk: list[ast.stmt]
        items: list[tuple[list[ast.stmt], ast.expr, ast.Name, list[ast.stmt] | None]]
        with_blk: list[ast.stmt] | None
        items = []
        for item in node.items:
            ctx_blk, ctx_e = self.split_expr(item.context_expr)
            tmp_l, tmp_s = self.tmp_gen()
            if item.optional_vars is not None:
                with_blk = self.resolve_single_Assign(item.optional_vars, tmp_l, item)
                items.append((ctx_blk, ctx_e, tmp_s, with_blk))
            else:
                items.append((ctx_blk, ctx_e, tmp_s, None))
        blk = self.visit_stmt_list(node.body)

        for idx in range(len(items) - 1, -1, -1):
            ctx_blk, ctx_e, tmp_s, with_blk = items[idx]
            if with_blk is not None:
                with_blk.extend(blk)
            else:
                with_blk = blk
            with_stmt = ast.AsyncWith(
                items=[ast.withitem(context_expr=ctx_e, optional_vars=tmp_s)], body=with_blk
            )
            ast.copy_location(with_stmt, node)
            blk = ctx_blk
            blk.append(with_stmt)
        return blk

    @override
    def visit_Raise(self, node: ast.Raise) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        e_elt, c_elt = None, None
        if node.exc is not None:
            e_blk, e_elt = self.split_expr(node.exc)
            blk.extend(e_blk)
        if node.cause is not None:
            c_blk, c_elt = self.split_expr(node.cause)
            blk.extend(c_blk)
        ins = ast.Raise(exc=e_elt, cause=c_elt)
        ast.copy_location(ins, node)
        blk.append(ins)
        return blk

    @override
    def visit_Try(self, node: ast.Try) -> list[ast.stmt]:
        stmts: list[ast.stmt]
        ins: ast.stmt
        handlers = []
        body_stmts = self.visit_stmt_list(node.body)
        stmts = []
        for old_handler in node.handlers:
            if old_handler.type is not None:
                t_blk, t_elt = self._visit_expr(old_handler.type)
                if not isinstance(t_elt, ast.Name):
                    tmp_l, tmp_s = self.tmp_gen()
                    ins = ast.Assign(targets=[tmp_s], value=t_elt)
                    ast.copy_location(ins, old_handler.type)
                    t_blk.append(ins)
                    t_elt = tmp_l
                stmts.extend(t_blk)
            else:
                t_elt = None
            handler = ast.ExceptHandler(
                type=t_elt, name=old_handler.name, body=self.visit_stmt_list(old_handler.body)
            )
            ast.copy_location(handler, old_handler)
            handlers.append(handler)
        ins = ast.Try(
            body=body_stmts,
            handlers=handlers,
            orelse=self.visit_stmt_list(node.orelse),
            finalbody=self.visit_stmt_list(node.finalbody),
        )
        ast.copy_location(ins, node)
        # A Try without handlers or finally is invalid Python AST. The
        # surrounding statement list already carries the lowered prefix.
        return stmts + [ins]

    @override
    def visit_Assert(self, node: ast.Assert) -> list[ast.stmt]:
        blk: list[ast.stmt]
        m_blk: list[ast.stmt]
        blk = []
        t_blk, t_elt = self.split_expr(node.test)
        m_blk, m_elt = [], None
        if node.msg is not None:
            m_blk, m_elt = self.split_expr(node.msg)
        m_args = [m_elt] if m_elt is not None else []
        blk.extend(t_blk)
        blk.extend(m_blk)
        tmp_l, tmp_s = self.tmp_gen()
        ins1 = ast.Assign(targets=[tmp_s], value=ast.UnaryOp(op=ast.Not(), operand=t_elt))
        ast.copy_location(ins1, node.test)
        blk.append(ins1)
        err_l, err_s = self.tmp_gen()
        exc_l, exc_s = self.tmp_gen()
        ins2 = ast.If(
            test=tmp_l,
            body=[
                ast.Assign(targets=[err_s], value=ast.Name(id="AssertionError", ctx=Load())),
                ast.Assign(targets=[exc_s], value=ast.Call(func=err_l, args=m_args, keywords=[])),
                ast.Raise(exc=exc_l),
            ],
            orelse=[],
        )
        ast.copy_location(ins2, node)
        blk.append(ins2)
        return blk

    def visit_stmt_list(self, stmts: Iterable[ast.stmt | ast.expr] | None) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        if stmts is None:
            return blk
        for stmt in stmts:
            if isinstance(stmt, ast.expr):
                cur_blk, _ = self.split_expr(stmt)
                blk.extend(cur_blk)
            else:
                lowered = self._visit_stmt(stmt)
                if isinstance(lowered, list):
                    blk.extend(lowered)
                else:
                    blk.append(lowered)
        return blk

    @override
    def visit_Match(self, node: ast.Match) -> ast.Match:
        # TODO: add support for pattern matching
        return node

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> list[ast.stmt]:
        bases: list[ast.expr]
        blk: list[ast.stmt]
        blk, bases = [], []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base)
            else:
                base_blk, base_elt = self.split_expr(base)
                blk.extend(base_blk)
                bases.append(base_elt)
        keywords = []
        for keyword in node.keywords:
            keyword_blk, keyword_value = self.split_expr(keyword.value)
            blk.extend(keyword_blk)
            keywords.append(ast.keyword(arg=keyword.arg, value=keyword_value))
        ins = ast.ClassDef(
            name=node.name,
            bases=bases,
            keywords=keywords,
            body=self.visit_stmt_list(node.body),
            decorator_list=node.decorator_list,
            type_params=[],
        )
        ast.copy_location(ins, node)
        blk.append(ins)
        return blk

    def _lower_decorators(
        self, decorators: list[ast.expr]
    ) -> tuple[list[ast.stmt], list[ast.expr]]:
        # Evaluation follows source order; application is reversed downstream.
        # Use the same expression/argument lowering as ordinary calls.
        block: list[ast.stmt]
        values: list[ast.expr]
        block, values = [], []
        for decorator in decorators:
            statements, value = self.split_expr(decorator)
            block.extend(statements)
            # Capture even a name now: a later factory can rebind that name.
            captured_load, captured_store = self.tmp_gen()
            capture = ast.Assign(targets=[captured_store], value=value)
            ast.copy_location(capture, decorator)
            block.append(capture)
            values.append(captured_load)
        return block, values

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> list[ast.stmt]:
        blk: list[ast.stmt]
        defaults: list[ast.stmt]
        ast.fix_missing_locations(node)
        blk, decorators = self._lower_decorators(node.decorator_list)
        defaults, args = self.visit_arguments(node.args)
        blk.extend(defaults)
        ins = ast.FunctionDef(
            name=node.name,
            args=args,
            body=self.visit_stmt_list(node.body),
            decorator_list=decorators,
            returns=node.returns,
            type_comment=node.type_comment,
            type_params=[],
        )
        ast.AST.__setattr__(ins, "_original_decorators", node.decorator_list)
        ast.copy_location(ins, node)
        blk.append(ins)
        return blk

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> list[ast.stmt]:
        blk: list[ast.stmt]
        defaults: list[ast.stmt]
        blk, decorators = self._lower_decorators(node.decorator_list)
        defaults, args = self.visit_arguments(node.args)
        blk.extend(defaults)
        ins = ast.AsyncFunctionDef(
            name=node.name,
            args=args,
            body=self.visit_stmt_list(node.body),
            decorator_list=decorators,
            returns=node.returns,
            type_comment=node.type_comment,
            type_params=[],
        )
        ast.AST.__setattr__(ins, "_original_decorators", node.decorator_list)
        ast.copy_location(ins, node)
        blk.append(ins)
        return blk

    @override
    def visit_arguments(self, node: ast.arguments) -> tuple[list[ast.stmt], ast.arguments]:
        blk: list[ast.stmt]
        defaults: list[ast.expr]
        kw_defaults: list[ast.expr | None]
        blk = []
        defaults = []
        for val in node.defaults:
            if isinstance(val, ast.Name):
                defaults.append(val)
            else:
                val_blk, val_elt = self.split_expr(val)
                blk.extend(val_blk)
                defaults.append(val_elt)
        kw_defaults = []
        for keyword_default in node.kw_defaults:
            if keyword_default is None or isinstance(keyword_default, ast.Name):
                kw_defaults.append(keyword_default)
            else:
                val_blk, val_elt = self.split_expr(keyword_default)
                blk.extend(val_blk)
                kw_defaults.append(val_elt)
        arguments = ast.arguments(
            posonlyargs=node.posonlyargs,
            args=node.args,
            vararg=node.vararg,
            kwonlyargs=node.kwonlyargs,
            kw_defaults=kw_defaults,
            kwarg=node.kwarg,
            defaults=defaults,
        )
        ast.copy_location(arguments, node)
        return blk, arguments

    @override
    def visit_Import(self, node: ast.Import) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        for alias in node.names:
            ins = ast.Import(names=[alias])
            ast.copy_location(ins, alias)
            blk.append(ins)
            self.import_stmts.add(ins)
        return blk

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        for alias in node.names:
            ins = ast.ImportFrom(module=node.module, names=[alias], level=node.level)
            ast.copy_location(ins, alias)
            blk.append(ins)
            self.import_stmts.add(ins)
        return blk

    @override
    def visit_Expr(self, node: ast.Expr) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk, elt = self.split_expr(node.value)
        if self.not_temp(elt):
            _, tmp_s = self.tmp_gen()
            ins = ast.Assign(targets=[tmp_s], value=elt)
            ast.copy_location(ins, node)
            blk.append(ins)
        return blk

    @override
    def visit_Global(self, node: ast.Global) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        for name in node.names:
            ins = ast.Global(names=[name])
            ast.copy_location(ins, node)
            blk.append(ins)
        return blk

    @override
    def visit_Nonlocal(self, node: ast.Nonlocal) -> list[ast.stmt]:
        blk: list[ast.stmt]
        blk = []
        for name in node.names:
            ins = ast.Nonlocal(names=[name])
            ast.copy_location(ins, node)
            blk.append(ins)
        return blk

    @override
    def visit_Pass(self, node: ast.Pass) -> ast.Pass:
        return node

    @override
    def visit_Break(self, node: ast.Break) -> ast.Break:
        return node

    @override
    def visit_Continue(self, node: ast.Continue) -> ast.Continue:
        return node

    @override
    def visit_Expression(self, node: ast.Expression) -> ast.Expression:
        raise TypeError("TAC expression roots have no statement-prefix context; use a Module root")

    @override
    def visit_Module(self, node: ast.Module) -> ast.Module:
        stmts: list[ast.stmt]
        self.reset()
        stmts = self.visit_stmt_list(node.body)
        const_stmts = [
            ast.Assign(targets=[ast.Name(id=n, ctx=ast.Store())], value=make_constant(v))
            for n, v in self.const_colle.dump()
        ]
        stmts = const_stmts + stmts
        mod = ast.Module(body=stmts, type_ignores=node.type_ignores)
        ast.copy_location(mod, node)
        ast.fix_missing_locations(mod)
        return mod

    @override
    def visit_Interactive(self, node: ast.Interactive) -> ast.Interactive:
        self.reset()
        inter = ast.Interactive(body=self.visit_stmt_list(node.body))
        ast.copy_location(inter, node)
        ast.fix_missing_locations(inter)
        return inter
