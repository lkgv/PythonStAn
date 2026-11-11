"""Historical core obligations migrated to the maintained AI domain and solver."""
from pathlib import Path
import ast
import pytest
from pythonstan.analysis.pointer.ai import AIState, AbsVal, AnalysisBudget, IRInterpreter, SolverConfig
from pythonstan.analysis.pointer.ai.heap import ContainerSlots, AIAddr, AbsObj, ObjKind
from pythonstan.analysis.pointer.ai.domains.ints import IntLattice
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.analysis.pointer.ai.config import Config
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite, AllocKind
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.ir.ir_statements import IRCopy, IRCall, JumpIfFalse
from legacy_ai_support import solve_source, terminal_state, int_exact, int_contains, analyze_program, build_source


class TestValueAndObject:
    def test_value_creation(self):
        lattice = AbsVal.from_int(42).get_int_lattice()
        assert lattice.get_consts() == frozenset({42})
        assert lattice.lo == lattice.hi == 42
        assert not lattice.may_be_negative() and not lattice.may_be_zero()
        assert lattice.may_be_positive()

    def test_value_merging(self):
        merged = AbsVal.from_int(5).join(AbsVal.from_int(10))
        assert merged.get_int_lattice().lo == 5
        assert merged.get_int_lattice().hi == 10
        mixed = merged.join(AbsVal.from_str("test"))
        assert int_contains(mixed, 5) and int_contains(mixed, 10)
        assert AbsVal.from_str("test").leq(mixed)

    def test_container_values(self):
        empty = ContainerSlots.empty_sequence()
        assert empty.length.lo == empty.length.hi == 0
        assert empty.get_elem().is_bottom()
        populated = empty.append(AbsVal.from_int(42))
        assert populated.length.lo == populated.length.hi == 1
        assert int_exact(populated.get_elem(), 42)
        assert empty.length.hi == 0


class TestAbstractState:
    def test_state_creation(self):
        state = AIState()
        config = Config(context_policy="1-cfa")
        assert state.env == {} and state.heap == {}
        assert config.context_policy == "1-cfa"
        assert config.to_solver_config().max_iterations == 100

    def test_variable_tracking(self):
        initial = AIState()
        updated = initial.set_var("x", AbsVal.from_int(42))
        assert int_exact(updated.get_var("x"), 42)
        assert "x" not in initial.env

    def test_context_sensitivity(self, tmp_path):
        with analyze_program(tmp_path, "def identity(x):\n return x\na=identity(1)\nb=identity(2)\n") as program:
            assert program.constants("a") == {1}
            assert program.constants("b") == {2}
            contexts = program.ai.context_summaries["subject.identity"]
            assert len(contexts) == 2
            assert {frozenset(o.value for o in summary.ret if hasattr(o, "value")) for _, summary in contexts} == {frozenset({1}), frozenset({2})}


class TestAbstractInterpreter:
    def test_visit_assign(self):
        interpreter = IRInterpreter(None, AnalysisBudget())
        result = interpreter.transfer(IRCopy(ast.parse("x=y").body[0]),
                                      AIState().set_var("y", AbsVal.from_int(42)), SummaryAccumulator())
        assert int_exact(result.states[0][1].get_var("x"), 42)

    def test_visit_call(self):
        summary = SummaryAccumulator()
        result = IRInterpreter(None, AnalysisBudget()).transfer(
            IRCall(ast.parse("result = test_func(arg)").body[0]),
            AIState().set_var("arg", AbsVal.from_int(5)), summary)
        assert result.states[0][1].get_var("result").addr_top
        assert "unknown_call" in summary.to_summary().incomplete_reasons

    def test_special_function_calls(self):
        obj = AbstractObject(CallStringContext(), AllocSite("list", AllocKind.LIST))
        addr = AIAddr.from_kcfa_obj(obj)
        slots = ContainerSlots(length=IntLattice.from_interval(2, 5), may_be_empty=False)
        state = AIState().set_obj(addr, AbsObj(addr, ObjKind.LIST, container_slots=slots))
        state = state.set_var("my_list", AbsVal.from_addrs(frozenset({obj})))
        final = terminal_state(solve_source("length = len(my_list)", state))
        length = final.get_var("length").get_int_lattice()
        assert length.lo == 2 and length.hi == 5


class TestAbstractInterpretationSolver:
    def test_solver_creation(self):
        config = Config(context_policy="1-cfa")
        solver = solve_source("x=1", config=config.to_solver_config())
        assert solver.config.max_iterations == 100
        assert int_exact(terminal_state(solver).get_var("x"), 1)

    def test_cfg_building(self):
        solver = solve_source("x=a\nif x:\n y=b\nz=1\n")
        branches = [b for b in solver.cfg.blks if any(isinstance(s, JumpIfFalse) for s in b.stmts)]
        assert len(branches) == 1
        assert len(solver.cfg_adapter.get_successors(branches[0].idx)) == 2
        assert int_exact(terminal_state(solver).get_var("z"), 1)


class TestBenchmarkIntegration:
    @pytest.mark.parametrize("benchmark_file", ["control_flow.py", "dataflow.py", "callgraph.py"])
    def test_analyze_benchmark(self, benchmark_file, tmp_path):
        path = Path(__file__).resolve().parents[2] / "benchmark" / benchmark_file
        with analyze_program(tmp_path, path.read_text()) as program:
            assert program.query.get_statistics()["converged"] == 1
            assert program.ai.summaries
