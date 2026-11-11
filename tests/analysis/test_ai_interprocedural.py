"""Historical caller/callee value obligations through the independent AI pass."""
from legacy_ai_support import analyze_program


def return_constants(summary):
    return {obj.value for obj in summary.ret if hasattr(obj, "value")}


class TestInterproceduralAnalysis:
    def test_simple_function_call_propagation(self, tmp_path):
        # The old fixture supplied precomputed x_plus_one_val=6; it never
        # implemented addition. Retain that value-transfer obligation explicitly.
        source = "def add_one(x):\n x_plus_one_val=6\n return x_plus_one_val\na=5\nb=add_one(a)\n"
        with analyze_program(tmp_path, source) as program:
            assert 6 in program.constants("b")
            assert 6 in return_constants(program.ai.get_summary("subject.add_one"))
            assert ("subject", "subject.add_one") in program.edges

    def test_recursive_function_analysis(self, tmp_path):
        source = "def recursive(flag):\n if flag:\n  return recursive(False)\n return 0\nx=recursive(True)\n"
        with analyze_program(tmp_path, source, ai_options={"max_recursion_depth": 2}) as program:
            assert ("subject.recursive", "subject.recursive") in program.edges
            assert 0 in program.constants("x")
            summary = program.ai.get_summary("subject.recursive")
            assert 0 in return_constants(summary)
            # True -> False needs only two levels. Precise boolean refinement
            # prunes the second call's recursive arm, so a depth-two budget is
            # sufficient; the former forced-incomplete expectation was stale.
            assert summary.is_complete, summary.incomplete_reasons
            assert "max_recursion_depth" not in summary.incomplete_reasons

    def test_actual_recursive_depth_cutoff_remains_open(self, tmp_path):
        # Static-only witness: the recursive arm preserves True and does not
        # terminate concretely. Never execute this target as a dynamic oracle.
        source = "def recursive(flag):\n if flag:\n  return recursive(flag)\n return 0\nx=recursive(True)\n"
        with analyze_program(tmp_path, source, ai_options={"max_recursion_depth": 2}) as program:
            summary = program.ai.get_summary("subject.recursive")
            assert not summary.is_complete
            assert "max_recursion_depth" in summary.incomplete_reasons


class TestContextSensitiveInterproceduralAnalysis:
    def test_multiple_call_sites(self, tmp_path):
        with analyze_program(tmp_path, "def identity(x):\n return x\na=identity(5)\nb=identity(10)\n") as program:
            assert program.constants("a") == {5}
            assert program.constants("b") == {10}
            contexts = program.ai.context_summaries["subject.identity"]
            assert len(contexts) == 2
            assert {frozenset(return_constants(s)) for _, s in contexts} == {frozenset({5}), frozenset({10})}

    def test_nested_calls_with_context(self, tmp_path):
        source = "def inner(x):\n return x\ndef outer(x):\n return inner(x)\nresult=outer(42)\n"
        with analyze_program(tmp_path, source) as program:
            assert program.constants("result") == {42}
            assert 42 in return_constants(program.ai.get_summary("subject.outer"))
            assert ("subject.outer", "subject.inner") in program.edges
