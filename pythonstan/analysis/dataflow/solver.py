from typing import Generic, TypeVar, Tuple, Dict, Any
from .analysis import DataflowAnalysis
from abc import ABC, abstractclassmethod
from pythonstan.graph.cfg.models import BaseBlock


Fact = TypeVar("Fact")


class Solver(ABC, Generic[Fact]):
    @classmethod
    def init(cls, analysis: DataflowAnalysis[Fact]
             ) -> Tuple[Dict[BaseBlock, Fact], Dict[BaseBlock, Fact]]:
        if analysis.is_forward:
            in_facts, out_facts = cls.init_forward(analysis)
        else:
            in_facts, out_facts = cls.init_backward(analysis)
        return in_facts, out_facts

    @classmethod
    def init_forward(cls, analysis: DataflowAnalysis[Fact]
             ) -> Tuple[Dict[BaseBlock, Fact], Dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_scope().cfg
        for node in cfg.blks:
            if node == cfg.entry_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                in_facts[node] = analysis.new_init_fact()
                out_facts[node] = analysis.new_init_fact()        
        return in_facts, out_facts
    
    @classmethod
    def init_backward(cls, analysis: DataflowAnalysis[Fact]
             ) -> Tuple[Dict[BaseBlock, Fact], Dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_scope().cfg
        for node in cfg.blks:
            if node == cfg.super_exit_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                in_facts[node] = analysis.new_init_fact()
                out_facts[node] = analysis.new_init_fact()        
        return in_facts, out_facts
    
    @classmethod
    def solve(cls, analysis: DataflowAnalysis[Fact]):
        in_facts, out_facts = cls.init(analysis)
        if analysis.is_forward:
            cls.solve_forward(analysis, in_facts, out_facts)
        else:
            cls.solve_backward(analysis, in_facts, out_facts)
        return in_facts, out_facts

    @abstractclassmethod
    def solve_forward(cls, analysis: DataflowAnalysis[Fact],
                      in_facts: Dict[BaseBlock, Fact],
                      out_facts: Dict[BaseBlock, Fact]):
        pass

    @abstractclassmethod
    def solve_backward(cls, analysis: DataflowAnalysis[Fact],
                       in_facts: Dict[BaseBlock, Fact],
                       out_facts: Dict[BaseBlock, Fact]):
        pass


class WorklistSolver(Solver[Fact], Generic[Fact]):
    @classmethod
    def init_forward(cls, analysis: DataflowAnalysis[Fact]
                     ) -> Tuple[Dict[BaseBlock, Fact], Dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_scope().cfg
        entry = cfg.entry_blk
        for node in cfg.blks:
            if node == entry:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                if cfg.in_degree_of(node) == 1:
                    e = cfg.in_edges_of(node)[0]
                    if not analysis.need_transfer_edge(e):
                        src = e.start
                        if out_facts[src] is None:
                            out_facts[src] = analysis.new_init_fact()
                        in_facts[node] = out_facts[src]
                else:
                    in_facts[node] = analysis.new_init_fact()
                if out_facts[node] is None:
                    out_facts[node] = analysis.new_init_fact()
    
    @classmethod
    def solve_forward(cls, analysis: DataflowAnalysis[Fact],
                      in_facts: Dict[BaseBlock, Fact],
                      out_facts: Dict[BaseBlock, Fact]):
        cfg = analysis.get_scope().cfg
        work_list = {blk for blk in cfg.blks if blk != cfg.entry_blk}
        while len(work_list) > 0:
            cur = work_list.pop()
            fact_in = in_facts[cur]
            if cfg.in_degree_of(cur) > 1:
                for e in cfg.in_edges_of(cur):
                    fact = out_facts[e.start]
                    if analysis.need_transfer_edge(e):
                        fact = analysis.transfer_edge(e, fact)
                    fact_in = analysis.meet(fact, fact_in)
            elif cfg.in_degree_of(cur) == 1:
                e = cfg.in_edges_of(cur)[0]
                if analysis.need_transfer_edge(e):
                    fact_in = analysis.transfer_edge(e, out_facts[e.start])
                    in_facts[cur] = fact_in
            fact_out = analysis.transfer_node(cur, fact_in)
            if fact_out != out_facts[cur]:
                out_facts[cur] = fact_out
                work_list.update(cfg.succs_of(cur))

    @classmethod
    def init_backward(cls, analysis: DataflowAnalysis[Fact]
                      ) -> Tuple[Dict[BaseBlock, Fact], Dict[BaseBlock, Fact]]:
        in_facts, out_facts = {}, {}
        cfg = analysis.get_scope().cfg
        entry = cfg.entry_blk
        for node in cfg.blks:
            if node == cfg.super_exit_blk:
                in_facts[node] = analysis.new_boundary_fact()
                out_facts[node] = analysis.new_boundary_fact()
            else:
                if cfg.out_degree_of(node) == 1:
                    e = cfg.out_edges_of(node)[0]
                    if not analysis.need_transfer_edge(e):
                        tgt = e.end
                        if in_facts[tgt] is None:
                            in_facts[tgt] = analysis.new_init_fact()
                        out_facts[node] = in_facts[tgt]
                else:
                    out_facts[node] = analysis.new_init_fact()
                if in_facts[node] is None:
                    in_facts[node] = analysis.new_init_fact()
    
    @classmethod
    def solve_backward(cls, analysis: DataflowAnalysis[Fact],
                       in_facts: Dict[BaseBlock, Fact],
                       out_facts: Dict[BaseBlock, Fact]):
        cfg = analysis.get_scope().cfg
        work_list = {blk for blk in cfg.blks if blk != cfg.super_exit_blk}
        while len(work_list) > 0:
            cur = work_list.pop()
            fact_out = out_facts[cur]
            if cfg.out_degree_of(cur) > 1:
                for e in cfg.out_edges_of(cur):
                    fact = out_facts[e.end]
                    if analysis.need_transfer_edge(e):
                        fact = analysis.transfer_edge(e, fact)
                    fact_out = analysis.meet(fact, fact_out)
            elif cfg.out_degree_of(cur) == 1:
                e = cfg.out_edges_of(cur)[0]
                if analysis.need_transfer_edge(e):
                    fact_out = analysis.transfer_edge(e, in_facts[e.end])
                    out_facts[cur] = fact_out
            fact_in = analysis.transfer_node(cur, fact_out)
            if fact_in != in_facts[cur]:
                in_facts[cur] = fact_in
                work_list.update(cfg.preds_of(cur))
