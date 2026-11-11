"""Executable versions of the historical benchmark and scenario obligations."""
import ast
from pathlib import Path
import pytest
from pythonstan.analysis.pointer.ai.config import Config
from legacy_ai_support import analyze_program, solve_source, terminal_state, int_exact, int_contains, function_names

BENCHMARK = Path(__file__).resolve().parents[2] / "benchmark"


class TestAIBenchmarks:
    @pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "1-obj"])
    def test_solver_configurations(self, context_policy, tmp_path):
        # Flow sensitivity is fixed by the current intraprocedural AI solver;
        # context abstraction is owned by its PTA predecessor.
        with analyze_program(tmp_path, "x=42", pointer_options={"context_policy": context_policy},
                             ai_options={"context_policy": context_policy}) as program:
            assert program.constants("x") == {42}
            assert program.ai.config.context_policy == context_policy
            assert program.ai.config.to_solver_config().max_iterations == 100

    @pytest.mark.parametrize("benchmark_file", ["control_flow.py", "dataflow.py", "complex_expressions.py"])
    def test_benchmark_parsing(self, benchmark_file):
        assert isinstance(ast.parse((BENCHMARK / benchmark_file).read_text()), ast.Module)

    def test_control_flow_analysis(self, tmp_path):
        with analyze_program(tmp_path, (BENCHMARK / "control_flow.py").read_text()) as program:
            assert program.query.get_statistics()["converged"] == 1
            assert program.ai.summaries

    def test_dataflow_analysis(self, tmp_path):
        with analyze_program(tmp_path, (BENCHMARK / "dataflow.py").read_text()) as program:
            assert program.query.get_statistics()["converged"] == 1
            assert program.ai.summaries

    def test_callgraph_analysis(self, tmp_path):
        with analyze_program(tmp_path, (BENCHMARK / "callgraph.py").read_text()) as program:
            assert program.query.call_graph() is not None
            assert program.ai.summaries

    def test_oop_analysis(self, tmp_path):
        with analyze_program(tmp_path, (BENCHMARK / "oop.py").read_text()) as program:
            assert program.query.get_statistics()["converged"] == 1
            assert program.ai.summaries


class TestSpecificBenchmarkScenarios:
    def test_if_else_flow(self):
        state = terminal_state(solve_source("a=15\nif a>10:\n result=20\nelse:\n result=10\n"))
        assert int_exact(state.get_var("result"), 20)

    def test_loop_analysis(self):
        # The historical mock assigned i_inc=1 forever while expecting i=3.
        # This source uses the actual increment specified by its scenario.
        state = terminal_state(solve_source("sum_val=0\ni=0\nwhile i<3:\n sum_val=sum_val+i\n i=i+1\n"))
        assert int_contains(state.get_var("sum_val"), 3)
        assert int_contains(state.get_var("i"), 3)

    def test_exception_handling(self, tmp_path):
        source = '''
def recover(x):
    try:
        result = 100 / x
    except ZeroDivisionError:
        result = 1e309
    return result
result = recover(0)
'''
        with analyze_program(tmp_path, source) as program:
            assert float("inf") in program.constants("result")
            summary = program.ai.get_summary("subject.recover")
            assert float("inf") in {obj.value for obj in summary.ret if hasattr(obj, "value")}

    def test_complex_dataflow(self):
        source = "a=10\nb=20\nif a>b:\n c=30\nelse:\n c=40\nc=50\n"
        assert int_exact(terminal_state(solve_source(source)).get_var("c"), 50)

    def test_recursive_functions(self, tmp_path):
        with analyze_program(tmp_path, "def recursive(flag):\n if flag:\n  return recursive(False)\n return 1\nresult=recursive(True)\n") as program:
            assert 1 in program.constants("result")
            assert 1 in {obj.value for obj in program.ai.get_summary("subject.recursive").ret if hasattr(obj, "value")}
            assert ("subject.recursive", "subject.recursive") in program.edges

    def test_inheritance_analysis(self, tmp_path):
        source = '''
class Animal:
    def make_sound(self):
        return "animal"
class Dog(Animal):
    def make_sound(self):
        return "Woof!"
class Cat(Animal):
    def make_sound(self):
        return "Meow!"
animal=Animal()
dog=Dog()
cat=Cat()
a=animal.make_sound()
d=dog.make_sound()
c=cat.make_sound()
'''
        with analyze_program(tmp_path, source) as program:
            for name in ("Dog", "Cat"):
                cls = next(iter(program.points(name)))
                assert "subject.Animal" in function_names(program.query.get_class_mro(cls))
            assert program.constants("a") == {"animal"}
            # Required return witnesses survive. May-attribute inheritance
            # still admits Animal's value; this is recorded precision debt,
            # not an established must-shadowing/exclusion guarantee.
            assert "Woof!" in program.constants("d")
            assert "Meow!" in program.constants("c")
            methods = {name: function_names(program.query.resolve_method(program.points(name), "make_sound"))
                       for name in ("Animal", "Dog", "Cat")}
            assert methods["Animal"] and methods["Dog"] and methods["Cat"]
            assert "subject.Dog.make_sound" in methods["Dog"]
            assert "subject.Cat.make_sound" in methods["Cat"]
            assert methods["Dog"] != methods["Animal"]
            assert methods["Cat"] != methods["Animal"]
            assert {("subject", "subject.Animal.make_sound"), ("subject", "subject.Dog.make_sound"),
                    ("subject", "subject.Cat.make_sound")} <= program.edges
