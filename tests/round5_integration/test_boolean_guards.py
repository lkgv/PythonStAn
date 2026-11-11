import pytest
from pythonstan.analysis.pointer.ai import AIState, AbsVal
from tests.analysis.legacy_ai_support import solve_source, terminal_state

@pytest.mark.parametrize('flag,expected', [(True,10),(False,20)])
def test_exact_boolean_guard_excludes_infeasible_arm(flag,expected):
    state = AIState().set_var('flag', AbsVal.bool_true() if flag else AbsVal.bool_false())
    final = terminal_state(solve_source('if flag:\n result=10\nelse:\n result=20',initial_state=state))
    assert final.get_var('result').get_int_lattice().get_consts() == frozenset({expected})

def test_unknown_guard_preserves_both_arms():
    final = terminal_state(solve_source('if flag:\n result=10\nelse:\n result=20',initial_state=AIState().set_var('flag',AbsVal.top())))
    assert final.get_var('result').get_int_lattice().get_consts() == frozenset(range(10,21))
