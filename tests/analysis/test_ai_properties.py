"""Finite production-domain properties replacing the removed mock prototype.

These deterministic checks retain the historical obligations and run without
hypothesis. They are regression evidence, not a proof for arbitrary Python.
"""
from itertools import product
import pytest
from pythonstan.analysis.pointer.ai import AIState, AbsVal, SolverConfig
from tests.analysis.legacy_ai_support import analyze_program, int_contains, solve_source, terminal_state


def values():
    return [AbsVal.bottom(), *(AbsVal.from_int(i) for i in range(-2, 3)),
            AbsVal.from_ints(frozenset({-2, 0, 2})), AbsVal.top()]


class TestMonotonicityProperties:
    def test_precision_monotonicity(self):
        for left, right in product(values(), repeat=2):
            joined = left.join(right)
            assert left.leq(joined) and right.leq(joined)
            assert joined.leq(right.join(left)) and right.join(left).leq(joined)

    def test_singleton_ratio_monotonicity(self):
        # Enlarging a finite value set never creates a singleton constant.
        for left, right in product(range(4), repeat=2):
            consts = AbsVal.from_int(left).join(AbsVal.from_int(right)).get_int_lattice().get_consts()
            assert {left, right} <= set(consts)
            assert (len(consts) == 1) == (left == right)

    def test_alias_density_properties(self, tmp_path):
        with analyze_program(tmp_path, 'class Box: pass\na=Box()\nb=a\nc=Box()\n') as run:
            for x, y in product(('a', 'b', 'c'), repeat=2):
                assert run.query.may_alias(x, y, run.module) == run.query.may_alias(y, x, run.module)
            assert run.query.may_alias('a', 'b', run.module)
            assert not run.query.may_alias('a', 'c', run.module)


class TestStabilityProperties:
    @pytest.mark.parametrize('partition', [False, True])
    def test_configuration_stability(self, partition):
        configuration = SolverConfig(enable_trace_partitioning=partition)
        results = [terminal_state(solve_source('x=3\ny=x+2', config=configuration)) for _ in range(2)]
        # Fresh frontend runs allocate distinct IR identities; compare the
        # complete scalar-domain projection rather than object identity.
        projections = [{name: value.components for name, value in state.env.items()} for state in results]
        assert projections[0] == projections[1]
        assert int_contains(results[0].get_var('y'), 5)

    def test_repeated_analysis_stability(self, tmp_path):
        source = 'def identity(x): return x\na=[]\nb=identity(a)\n'
        projections = []
        for _ in range(3):
            with analyze_program(tmp_path, source) as run:
                projections.append((run.edges, run.query.may_alias('a', 'b', run.module)))
        assert projections[0] == projections[1] == projections[2]
        assert projections[0][1]


class TestSoundnessProperties:
    def test_over_approximation_soundness(self):
        for a, b in product(range(-3, 4), repeat=2):
            state = AIState().set_var('x', AbsVal.from_ints(frozenset({a, b})))
            result = terminal_state(solve_source('y=x*2+1', initial_state=state)).get_var('y')
            assert int_contains(result, a*2+1) and int_contains(result, b*2+1)

    def test_alias_transitivity_property(self, tmp_path):
        # May-alias is symmetric, NOT transitive. Keep the old test name but
        # enforce the correct relation on an actual branching program.
        source = 'class Box: pass\na=Box()\nc=Box()\nif unknown: b=a\nelse: b=c\n'
        with analyze_program(tmp_path, source) as run:
            assert run.query.may_alias('a', 'b', run.module)
            assert run.query.may_alias('b', 'c', run.module)
            assert not run.query.may_alias('a', 'c', run.module)


class TestStateMachineProperties:
    def test_ai_analysis_state_machine(self):
        state = AIState()
        expected = set()
        for n in (2, 1, 2, -1, 0, 3):
            previous = state.copy()
            state = state.weak_set_var('x', AbsVal.from_int(n))
            expected.add(n)
            assert previous.leq(state)
            assert all(int_contains(state.get_var('x'), v) for v in expected)
            snapshot = state.copy()
            state = state.set_var('y', state.get_var('x'))
            assert snapshot.get_var('y') == previous.get_var('y')


class TestBasicProperties:
    def test_monotonicity_basic(self):
        a = AIState().set_var('x', AbsVal.from_int(1))
        b = AIState().set_var('x', AbsVal.from_int(2))
        assert a.leq(a.join(b)) and b.leq(a.join(b))

    def test_stability_basic(self):
        a = AbsVal.from_ints(frozenset({1, 2}))
        assert a.join(a) == a

    def test_soundness_basic(self):
        for value in values():
            assert value.leq(AbsVal.top())
