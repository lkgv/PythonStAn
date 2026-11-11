from typing import List, Dict, Optional, Tuple, Any
from queue import Queue

from ..analysis import AnalysisConfig
from .transform import Transform
from pythonstan.graph.cfg import *
from pythonstan.world import World
from pythonstan.ir import *


__all__ = ['STAGE_NAME', 'BlockCFG', 'BlockCFGBuilder']
STAGE_NAME = 'block cfg'


class BlockCFG(Transform):
    transformer: 'BlockCFGBuilder'

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)

    def transform(self, module: IRModule):
        module_ir: List[IRStatement] = World().scope_manager.get_ir(module, "ir")
        q = Queue()
        q.put((module, module_ir))
        while not q.empty():
            cur_scope, cur_ir = q.get()
            transformer = BlockCFGBuilder(cur_scope)
            transformer.build_graph(cur_ir)
            World().scope_manager.set_ir(cur_scope, STAGE_NAME, transformer.cfg)
            for subscope in World().scope_manager.get_subscopes(cur_scope):
                if World().scope_manager.get_ir(subscope, STAGE_NAME) is None:
                    subscope_ir = World().scope_manager.get_ir(subscope, "ir")
                    q.put((subscope, subscope_ir))


class BaseBlockHelper:
    @staticmethod
    def get_goto_stmts(blk: BaseBlock) -> List[IRStatement]:
        return [s for s in blk.get_stmts() if isinstance(s, (Goto, JumpIfTrue, JumpIfFalse))]

    @staticmethod
    def will_directly_jump(blk: BaseBlock) -> bool:
        stmts = blk.get_stmts()
        return len(stmts) > 0 and isinstance(stmts[-1], (Goto, IRReturn, IRRaise))

    @staticmethod
    def will_return(blk: BaseBlock) -> bool:
        stmts = blk.get_stmts()
        return len(stmts) > 0 and any([s for s in stmts if isinstance(s, IRReturn)])

    @staticmethod
    def retrive_label(blk: BaseBlock) -> Optional[Label]:
        stmts = blk.get_stmts()
        for stmt in stmts:
            if isinstance(stmt, IRCatchException):
                # Protected-region declarations are metadata. A handler is
                # entered only by an exceptional transfer from its operation.
                continue
            if isinstance(stmt, Label):
                return stmt
        return None


class BlockCFGBuilder:
    cfg: ControlFlowGraph
    scope: IRScope
    lab2blk: Dict[Label, BaseBlock]
    blk_count: int

    def __init__(self, scope: IRScope):
        self.scope = scope
        self.lab2blk = {}
        self.blk_count = 0

    def split_stmts(self, stmts: List[IRStatement]) -> List[List[IRStatement]]:
        cur_blk = []
        ret_blks = [cur_blk]
        for stmt in stmts:
            if isinstance(stmt, IRCatchException):
                # Protected-region declarations are metadata. A handler is
                # entered only by an exceptional transfer from its operation.
                continue
            if isinstance(stmt, Label):
                cur_blk = [stmt]
                ret_blks.append(cur_blk)
            elif isinstance(stmt, (Goto, JumpIfTrue, JumpIfFalse,
                                   IRCatchException, IRReturn, IRRaise)):
                cur_blk.append(stmt)
                cur_blk = []
                ret_blks.append(cur_blk)
            else:
                cur_blk.append(stmt)
                if ((getattr(stmt, "exception_handlers", ()) and not isinstance(stmt, IRPass))
                        or getattr(stmt, "suppressed_label", None) is not None):
                    # Each potentially raising operation owns its exceptional
                    # successors; later statements cannot execute before them.
                    cur_blk = []
                    ret_blks.append(cur_blk)
        return ret_blks

    def build_graph(self, stmts: List[IRStatement]):
        entry_blk = self.new_blk([])
        self.cfg = ControlFlowGraph(entry_blk)
        stmts_list = self.split_stmts(stmts)
        blk_list = []
        for cur_stmts in stmts_list:
            blk = self.new_blk(cur_stmts)
            blk_label = BaseBlockHelper.retrive_label(blk)
            if blk_label is not None:
                self.lab2blk[blk_label] = blk
                # Populate CFG label map so downstream analyses can resolve jumps
                self.cfg.add_label(blk_label, blk)
            blk_list.append(blk)
        for blk in blk_list:
            self.cfg.add_blk(blk)
            if blk.idx == 1:
                self.cfg.add_edge(NormalEdge(entry_blk, blk))
        for index, blk in enumerate(blk_list):
            if index + 1 < len(blk_list) and not BaseBlockHelper.will_directly_jump(blk):
                self.cfg.add_edge(NormalEdge(blk, blk_list[index + 1]))
            for stmt in BaseBlockHelper.get_goto_stmts(blk):
                tgt_blk = self.lab2blk.get(stmt.label, None)
                if tgt_blk is None:
                    continue
                if isinstance(stmt, Goto):
                    self.cfg.add_edge(NormalEdge(blk, tgt_blk))
                elif isinstance(stmt, JumpIfTrue):
                    self.cfg.add_edge(IfEdge(blk, tgt_blk, stmt.test, True))
                elif isinstance(stmt, JumpIfFalse):
                    self.cfg.add_edge(IfEdge(blk, tgt_blk, stmt.test, False))
            for stmt in blk.get_stmts():
                suppressed = getattr(stmt, "suppressed_label", None)
                if suppressed is not None and suppressed in self.lab2blk:
                    # __exit__ True skips the saved re-raise; False follows the
                    # ordinary edge. Keep both successors materialized even
                    # though the interpreter selects the labeled channel.
                    from ast import Name, Load
                    self.cfg.add_edge(IfEdge(blk,self.lab2blk[suppressed],
                                             Name(id="@context_exit_suppressed",ctx=Load()),True))
                if isinstance(stmt, (Label, IRPass, IRReturn, Goto, JumpIfTrue, JumpIfFalse)):
                    continue
                for handler in getattr(stmt, "exception_handlers", ()):
                    tgt_blk = self.lab2blk.get(handler.goto_label)
                    if tgt_blk is not None:
                        edge = ExceptionEdge(blk, tgt_blk, handler.exception)
                        self.cfg.add_edge(edge)
        for blk in blk_list:
            if BaseBlockHelper.will_return(blk):
                self.cfg.add_exit(blk)
        self.cfg.add_exit(blk_list[-1])
        self.cfg.add_super_exit_blk(self.new_blk([]))

    def new_blk(self, stmts) -> BaseBlock:
        blk = BaseBlock(self.blk_count, stmts)
        self.blk_count += 1
        return blk
