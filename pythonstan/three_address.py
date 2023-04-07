import ast
from ast import NodeTransformer, Store, Load
from pythonstan.utils import update_ctx, destructable, TempVarGenerator

# TODO: reorganize the contexts here


class ThreeAddressTransformer(NodeTransformer):
    tmp_gen = TempVarGenerator()

    def reset(self):
        self.tmp_gen = TempVarGenerator()
    
    def resolve_single_Assign(self, tgt, value, stmt):
        tblk, texp = self.visit(tgt)
        if isinstance(texp, ast.Name):
            ins = ast.Assign(
                targets=[texp],
                value=value
            )
            tblk.append(ins)
        # unpacking cannot be desugarred in semantic level (because of the existence of iterator)
        # only the situation that both sides has equal length can be splitted
        elif destructable(texp) and destructable(value) \
                and len(texp.elts) == len(value.elts) \
                and (not any([x for x in value.elts if isinstance(x, ast.Starred)])):
            for t, v in zip(texp.elts, value.elts):
                if isinstance(t, ast.Name):
                    ins = ast.Assign(targets=[t], value=v)
                    ast.copy_location(ins, stmt)
                    tblk.append(ins)
                else:
                    tmp_l, tmp_s = self.tmp_gen()
                    if isinstance(t, ast.Starred):
                        v = ast.List(elts=[update_ctx(v, Load())], ctx=Load())
                    ins1 = ast.Assign(targets=[tmp_s], value=v)
                    ast.copy_location(ins1, stmt)
                    ins2 = ast.Assign(targets=[t],value=tmp_l)
                    ast.copy_location(ins2, stmt)
                    tblk.extend([ins1, ins2])
        else:
            tmp_l, tmp_s = self.tmp_gen(ctxs=[Load(), Store()])
            ins1 = ast.Assign(targets=[tmp_s], value=value)
            ast.copy_location(ins1, stmt)
            ins2 = ast.Assign(targets=[texp], value=tmp_l)
            ast.copy_location(ins2, stmt)
            tblk.extend((ins1, ins2))
        return tblk

    def split_expr(self, exp):
        blk, e = self.visit(exp)
        if not (isinstance(e, ast.Name) or isinstance(e, ast.Constant) or \
                isinstance(e, ast.Starred)):
            (tmp_s,) = self.tmp_gen(ctxs=[Store()])
            ins = ast.Assign(targets=[tmp_s], value=e)
            ast.copy_location(ins, exp)
            blk.append(ins)
            ctx = Load() if not hasattr(exp, 'ctx') else exp.ctx
            e = self.tmp_gen.get(var=tmp_s, ctx=ctx)
        return blk, e
    
    def visit_BinOp(self, node):
        lblk, lval = self.split_expr(node.left)
        rblk, rval = self.split_expr(node.right)
        ins = ast.BinOp(left=lval, op=node.op, right=rval)
        ast.copy_location(ins, node)
        return lblk + rblk, ins
    
    def visit_UnaryOp(self, node):
        blk, val = self.split_expr(node.operand)
        ins = ast.UnaryOp(operand=val, op=node.op)
        ast.copy_location(ins, node)
        return blk, ins
    
    def visit_BoolOp(self, node):
        blk, elts = None, []
        test_list = [self.visit(val) for val in node.values]
        tmp_l, tmp_s = self.tmp_gen()
        for tmp_blk, tmp_elt in test_list[::-1]:
            ass_ins = ast.Assign(targets=[tmp_s], value=tmp_elt)
            ast.copy_location(ass_ins, node)
            tmp_blk.append(ass_ins)
            if blk is not None:
                if isinstance(node.op, ast.And):
                    tmp_test = tmp_l
                elif isinstance(node.op, ast.Or):
                    tmp_test = ast.UnaryOp(op=ast.Not(), operand=tmp_l)
                else:
                    raise NotImplementedError("The op is not a bool op!")
                tmp_blk.append(ast.If(test=tmp_test, body=blk, orelse=[]))
            blk = tmp_blk
            elts.insert(0, tmp_elt)
        return blk, tmp_l
    
    def visit_IfExp(self, node):
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
    
    def visit_Lambda(self, node):
        (f_load,) = self.tmp_gen(ctxs=[Load()])
        f_name = f_load.id
        blk = []
        new_func = ast.FunctionDef(
            name=f_name,
            args=node.args,
            body=[ast.Return(value=node.body)],
            decorator_list=[])
        blk.extend(self.visit_FunctionDef(new_func))
        return blk, f_load
    
    def visit_NamedExpr(self, node):
        AGGRESSIVE = True
        if AGGRESSIVE:
            v_blk, v_elt = self.visit(node.value)
            t_blk, t_elt = self.visit(node.target)
            ins = ast.Assign(targets=[t_elt], value=v_elt)
            ast.copy_location(ins, node)
            return v_blk + t_blk + [ins], v_elt
        else:
            v_blk, v_elt = self.split_expr(node.value)
            t_blk, t_elt = self.visit(node.target)
            ins = ast.NamedExpr(target=t_elt, value=v_elt)
            ast.copy_location(ins, node)
            return v_blk + t_blk, ins
    
    def visit_Tuple(self, node):
        blk, elts = [], []
        for old_elt in node.elts:
            if (not isinstance(node.ctx, Store)) or \
                (hasattr(old_elt, "ctx") and (not isinstance(old_elt.ctx, Store))):
                tmp_blk, tmp_elt = self.split_expr(old_elt)
            else:
                tmp_blk, tmp_elt = self.visit(old_elt)
            blk.extend(tmp_blk)
            elts.append(tmp_elt)
        exp = ast.Tuple(elts=elts, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp
    
    def visit_List(self, node):
        blk, elts = [], []
        for old_elt in node.elts:
            if isinstance(node.ctx, Load) or \
                (hasattr(old_elt, "ctx") and isinstance(old_elt.ctx, Load)):
                tmp_blk, tmp_elt = self.split_expr(old_elt)
            else:
                tmp_blk, tmp_elt = self.visit(old_elt)                
            blk.extend(tmp_blk)
            elts.append(tmp_elt)
        exp = ast.List(elts=elts, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp
    
    def visit_Set(self, node):
        blk, elts = [], []
        for old_elt in node.elts:
            tmp_blk, tmp_elt = self.split_expr(old_elt)
            blk.extend(tmp_blk)
            elts.append(tmp_elt)
        exp = ast.Set(elts=elts)
        ast.copy_location(exp, node)
        return blk, exp
    
    def visit_Dict(self, node):
        blk, keys, values = [], [], []
        for k, v in zip(node.keys, node.values):
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
    
    def visit_ListComp(self, node):
        pass
    
    def visit_Await(self, node):
        blk, elt = self.visit(node.value)
        exp = ast.Await(value=elt)
        ast.copy_location(exp, node)
        return blk, exp
    
    def visit_Yield(self, node):
        blk, elt = [], None
        if node.value is not None:
            blk, elt = self.visit(node.value)
        exp = ast.Yield(value=elt)
        ast.copy_location(exp, node)
        return blk, exp

    def visit_YieldFrom(self, node):
        blk, elt = self.visit(node.value)
        exp = ast.YieldFrom(value=elt)
        ast.copy_location(exp, node)
        return blk, exp
    
    def visit_BoolOp(self, node):
        blk, elts = None, []
        test_list = [self.visit(val) for val in node.values]
        tmp_l, tmp_s = self.tmp_gen()
        for tmp_blk, tmp_elt in test_list[::-1]:
            ass_ins = ast.Assign(targets=[tmp_s], value=tmp_elt)
            ast.copy_location(ass_ins, node)
            tmp_blk.append(ass_ins)
            if blk is not None:
                if isinstance(node.op, ast.And):
                    tmp_test = tmp_l
                elif isinstance(node.op, ast.Or):
                    tmp_test = ast.UnaryOp(op=ast.Not(), operand=tmp_l)
                else:
                    raise NotImplementedError("The op is not a bool op!")
                tmp_blk.append(ast.If(test=tmp_test, body=blk, orelse=[]))
            blk = tmp_blk
            elts.insert(0, tmp_elt)
        return blk, tmp_l

    def visit_Compare(self, node):
        blk, elts = None, []
        values = [node.left] + node.comparators
        test_list = [self.split_expr(val) for val in values]
        tmp_l, tmp_s = self.tmp_gen()
        for i in range(len(test_list) - 1, -1, -1):
            tmp_blk, tmp_elt = test_list[i]
            if i > 0:
                _, lhs_elt = test_list[i - 1]
                tmp_test = ast.Compare(left=lhs_elt,
                                       ops=[node.ops[i - 1]],
                                       comparators=[tmp_elt])
                ass_ins = ast.Assign(targets=[tmp_s], value=tmp_test)
                ast.copy_location(ass_ins, node)
                tmp_blk.append(ass_ins)
                if blk is not None:
                    tmp_blk.append(ast.If(test=tmp_l, body=blk, orelse=[]))
            else:
                tmp_blk.extend(blk)
            blk = tmp_blk
            elts.insert(0, tmp_elt)
        return blk, tmp_l

    def visit_Call(self, node):
        blk, args, keywords = [], [], []
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
        ins = ast.Assign(
            targets=[tmp_s],
            value=ast.Call(func=func_elt, args=args, keywords=keywords))
        blk.append(ins)
        return blk, tmp_l

    def visit_Name(self, node):
        return [], node
        
    def visit_Constant(self, node):
        tmp_l, tmp_s = self.tmp_gen()
        ins = ast.Assign(targets=[tmp_s], value=node)
        return [ins], tmp_l

    def visit_Starred(self, node):
        if isinstance(node.ctx, ast.Load):
            blk, elt = self.split_expr(node.value)
        else:
            blk, elt = self.visit(node.value)
        exp = ast.Starred(value=elt, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp

    def visit_Attribute(self, node):
        blk, elt = self.split_expr(node.value)
        exp = ast.Attribute(value=elt, attr=node.attr, ctx=node.ctx)
        ast.copy_location(exp, node)
        return blk, exp
    
    def visit_Subscript(self, node):
        if isinstance(node.slice, ast.Slice):
            s_blk, s_elt = self.visit(node.slice)
        else:
            s_blk, s_elt = self.split_expr(node.slice)
        v_blk, v_elt = self.split_expr(node.value)
        exp = ast.Subscript(value=v_elt, slice=s_elt, ctx=node.ctx)
        ast.copy_location(exp, node)
        return v_blk + s_blk, exp

    def visit_Slice(self, node):
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
    
    def visit_Return(self, node):
        blk, v = [], None
        if node.value is not None:
            blk, v = self.split_expr(node.value)
        stmt = ast.Return(value=v)
        ast.copy_location(stmt, node)
        blk.append(stmt)
        return blk
    
    def visit_AugAssign(self, node):
        expand_stmt = ast.Assign(
            targets=[node.target],
            value=ast.BinOp(
                left=update_ctx(node.target, Load()),
                op=node.op,
                right=node.value))
        ast.copy_location(expand_stmt, node)
        return self.visit(expand_stmt)

    def visit_Assign(self, node):
        tgts = node.targets
        blk, exp = self.visit(node.value)
        for tgt in tgts[::-1]:
            tblk = self.resolve_single_Assign(tgt, exp, node)
            blk.extend(tblk)
        return blk
    
    def visit_stmt_list(self, stmts):
        blk = []
        if stmts is None:
            return blk
        for stmt in stmts:
            if isinstance(stmt, ast.expr):
                cur_blk, _ = self.split_expr(stmt)
                blk.extend(cur_blk)
            else:
                cur_blk = self.visit(stmt)
                blk.extend(cur_blk)
        return blk
    
    def visit_FunctionDef(self, node):
        ins = ast.FunctionDef(
            name=node.name,
            args=node.args,
            body=self.visit_stmt_list(node.body),
            decorator_list=node.decorator_list,
            returns=node.returns,
            type_comment=node.type_comment
        )
        ast.copy_location(ins, node)
        return [ins]

    def visit_Expr(self, node):
        blk, _ = self.split_expr(node.value)
        return blk