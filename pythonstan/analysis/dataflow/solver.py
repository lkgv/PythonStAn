from abc import ABC, abstractmethod
from collections import deque
from typing import ClassVar, override

from pythonstan.graph.cfg import BaseBlock

from .equations import DataflowEquations


class Solver[Fact](ABC):
    solver_dict: ClassVar[dict[str, object]] = {}

    @override
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.solver_dict[cls.__name__] = cls

    @classmethod
    def get_solver(cls, id: str) -> object:
        return cls.solver_dict[id]

    @classmethod
    def init(
        cls, analysis: DataflowEquations[Fact]
    ) -> tuple[dict[BaseBlock, Fact], dict[BaseBlock, Fact]]:
        if analysis.is_forward:
            in_facts, out_facts = cls.init_forward(analysis)
        else:
            in_facts, out_facts = cls.init_backward(analysis)
        return in_facts, out_facts

    @classmethod
    def init_forward(
        cls, analysis: DataflowEquations[Fact]
    ) -> tuple[dict[BaseBlock, Fact], dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_cfg()
        for node in cfg.blks:
            if node == cfg.entry_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                in_facts[node] = analysis.new_init_fact()
                out_facts[node] = analysis.new_init_fact()
        return in_facts, out_facts

    @classmethod
    def init_backward(
        cls, analysis: DataflowEquations[Fact]
    ) -> tuple[dict[BaseBlock, Fact], dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.cfg
        for node in cfg.blks:
            if node == cfg.super_exit_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                in_facts[node] = analysis.new_init_fact()
                out_facts[node] = analysis.new_init_fact()
        return in_facts, out_facts

    @classmethod
    def solve(
        cls, analysis: DataflowEquations[Fact]
    ) -> tuple[dict[BaseBlock, Fact], dict[BaseBlock, Fact]]:
        in_facts, out_facts = cls.init(analysis)
        if analysis.is_forward:
            cls.solve_forward(analysis, in_facts, out_facts)
        else:
            cls.solve_backward(analysis, in_facts, out_facts)
        return in_facts, out_facts

    @classmethod
    @abstractmethod
    def solve_forward(
        cls,
        analysis: DataflowEquations[Fact],
        in_facts: dict[BaseBlock, Fact],
        out_facts: dict[BaseBlock, Fact],
    ) -> None:
        pass

    @classmethod
    @abstractmethod
    def solve_backward(
        cls,
        analysis: DataflowEquations[Fact],
        in_facts: dict[BaseBlock, Fact],
        out_facts: dict[BaseBlock, Fact],
    ) -> None:
        pass


class WorklistSolver[Fact](Solver[Fact]):
    @classmethod
    @override
    def init_forward(
        cls, analysis: DataflowEquations[Fact]
    ) -> tuple[dict[BaseBlock, Fact], dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_cfg()
        for node in cfg.blks:
            if node == cfg.entry_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                if cfg.in_degree_of(node) == 1:
                    e = cfg.in_edges_of(node)[0]
                    if not analysis.need_transfer_edge(e):
                        src = e.src
                        if src not in out_facts:
                            out_facts[src] = analysis.new_init_fact()
                        in_facts[node] = out_facts[src]
                    else:
                        in_facts[node] = analysis.new_init_fact()
                else:
                    in_facts[node] = analysis.new_init_fact()
                if node not in out_facts:
                    out_facts[node] = analysis.new_init_fact()
        return in_facts, out_facts

    @classmethod
    @override
    def solve_forward(
        cls,
        analysis: DataflowEquations[Fact],
        in_facts: dict[BaseBlock, Fact],
        out_facts: dict[BaseBlock, Fact],
    ) -> None:
        cfg = analysis.get_cfg()
        work_list = deque(
            sorted((blk for blk in cfg.blks if blk != cfg.entry_blk), key=lambda blk: blk.idx)
        )
        pending = set(work_list)
        while work_list:
            cur = work_list.popleft()
            # A self-loop or a fact arriving during transfer must be able to
            # schedule this block again. Only duplicate *pending* work is joined.
            pending.remove(cur)
            fact_in = in_facts[cur]
            if cfg.in_degree_of(cur) > 0:
                for e in cfg.in_edges_of(cur):
                    fact = out_facts[e.src]
                    if analysis.need_transfer_edge(e):
                        fact = analysis.transfer_edge(e, fact)
                    fact_in = analysis.meet(fact, fact_in)
            in_facts[cur] = fact_in
            fact_out = analysis.transfer_node(cur, fact_in)
            if fact_out != out_facts[cur]:
                out_facts[cur] = fact_out
                for succ in cfg.succs_of(cur):
                    if succ not in pending:
                        pending.add(succ)
                        work_list.append(succ)

    @classmethod
    @override
    def init_backward(
        cls, analysis: DataflowEquations[Fact]
    ) -> tuple[dict[BaseBlock, Fact], dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_cfg()
        for node in cfg.blks:
            if node == cfg.super_exit_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                if cfg.out_degree_of(node) == 1:
                    e = cfg.out_edges_of(node)[0]
                    if not analysis.need_transfer_edge(e):
                        tgt = e.get_tgt()
                        if tgt not in in_facts:
                            in_facts[tgt] = analysis.new_init_fact()
                        out_facts[node] = in_facts[tgt]
                    else:
                        out_facts[node] = analysis.new_init_fact()
                else:
                    out_facts[node] = analysis.new_init_fact()
                if node not in in_facts:
                    in_facts[node] = analysis.new_init_fact()
        return in_facts, out_facts

    @classmethod
    @override
    def solve_backward(
        cls,
        analysis: DataflowEquations[Fact],
        in_facts: dict[BaseBlock, Fact],
        out_facts: dict[BaseBlock, Fact],
    ) -> None:
        cfg = analysis.get_cfg()
        work_list = deque(
            sorted(
                (blk for blk in cfg.blks if blk != cfg.super_exit_blk),
                key=lambda blk: blk.idx,
                reverse=True,
            )
        )
        pending = set(work_list)
        while work_list:
            cur = work_list.popleft()
            pending.remove(cur)
            fact_out = out_facts[cur]
            if cfg.out_degree_of(cur) > 0:
                for e in cfg.out_edges_of(cur):
                    fact = in_facts[e.tgt]
                    if analysis.need_transfer_edge(e):
                        fact = analysis.transfer_edge(e, fact)
                    fact_out = analysis.meet(fact, fact_out)
            out_facts[cur] = fact_out
            fact_in = analysis.transfer_node(cur, fact_out)
            if fact_in != in_facts[cur]:
                in_facts[cur] = fact_in
                for pred in cfg.preds_of(cur):
                    if pred not in pending:
                        pending.add(pred)
                        work_list.append(pred)
