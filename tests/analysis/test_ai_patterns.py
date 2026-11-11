"""Source-level versions of the historical AI pattern obligations."""
from legacy_ai_support import solve_source, terminal_state, int_exact, int_contains, analyze_program
from pythonstan.analysis.pointer.ai import AIState, AbsVal
from pythonstan.analysis.pointer.ai.heap import ContainerSlots


class TestVariablePropagation:
    def test_simple_assignment_chain(self):
        assert int_exact(terminal_state(solve_source("a=42\nb=a\nc=b")).get_var("c"), 42)

    def test_conditional_propagation(self):
        state = terminal_state(solve_source("if condition:\n z=10\nelse:\n z=20"))
        assert int_contains(state.get_var("z"), 10)
        assert int_contains(state.get_var("z"), 20)


class TestObjectHandling:
    def test_attribute_access(self, tmp_path):
        with analyze_program(tmp_path, "class Box: pass\nobj=Box()\nobj.attr=42\nx=obj.attr\n") as program:
            assert program.constants("x") == {42}
            assert program.query.field_of_objects(program.points("obj"), "attr")

    def test_container_operations(self, tmp_path):
        with analyze_program(tmp_path, "items=[]\nitems.append(42)\nx=items[0]\n") as program:
            assert 42 in program.constants("x")
        slots = ContainerSlots.empty_sequence().append(AbsVal.from_int(42))
        assert slots.length.lo == slots.length.hi == 1
        assert int_exact(slots.get_elem(), 42)


class TestFunctionCalls:
    def test_simple_function_call(self, tmp_path):
        with analyze_program(tmp_path, "def f(x):\n return x\nresult=f(42)\n") as program:
            assert ("subject", "subject.f") in program.edges
            assert program.constants("result") == {42}
            assert {obj.value for obj in program.ai.get_summary("subject.f").ret if hasattr(obj, "value")} == {42}

    def test_builtin_function_calls(self):
        state = terminal_state(solve_source("items=[1,2,3]\nlength=len(items)"))
        assert int_exact(state.get_var("length"), 3)


class TestControlFlow:
    def test_if_else_flow(self):
        state = terminal_state(solve_source("if flag:\n z=5\nelse:\n z=15"))
        assert int_contains(state.get_var("z"), 5)
        assert int_contains(state.get_var("z"), 15)


class TestContextSensitivity:
    def test_call_site_sensitivity(self, tmp_path):
        with analyze_program(tmp_path, "def identity(x):\n return x\na=identity(1)\nb=identity(2)\n") as program:
            assert program.constants("a") == {1}
            assert program.constants("b") == {2}
            assert len(program.ai.context_summaries["subject.identity"]) == 2

    def test_object_sensitivity(self, tmp_path):
        source = "class Box:\n def identity(self,x):\n  return x\na=Box()\nb=Box()\nx=a.identity(1)\ny=b.identity(2)\n"
        with analyze_program(tmp_path, source, pointer_options={"context_policy": "1-obj"}) as program:
            assert program.constants("x") == {1}
            assert program.constants("y") == {2}
            observations = {frozenset(obj.value for obj in summary.ret if hasattr(obj, "value"))
                            for _, summary in program.ai.context_summaries["subject.Box.identity"]}
            assert frozenset({1}) in observations and frozenset({2}) in observations
