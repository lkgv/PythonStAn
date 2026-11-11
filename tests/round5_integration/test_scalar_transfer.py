from itertools import product
from pythonstan.analysis.pointer.ai import AIState, AbsVal
from tests.analysis.legacy_ai_support import solve_source, terminal_state, int_contains, analyze_program
from pythonstan.analysis.pointer.kcfa.object import ConstantObject


def test_scalar_def_use_exact_values():
    result = terminal_state(solve_source('x=42\ny=x+1\nz=y*2'))
    assert result.get_var('y').get_int_lattice().get_consts() == frozenset({43})
    assert result.get_var('z').get_int_lattice().get_consts() == frozenset({86})


def test_finite_comparison_refines_guards_exactly():
    for a,b in product(range(-2,3), repeat=2):
        for symbol, expected in [('>',a>b), ('<',a<b), ('==',a==b), ('!=',a!=b), ('<=',a<=b), ('>=',a>=b)]:
            source=f'a={a}\nb={b}\nif a {symbol} b:\n result=10\nelse:\n result=20'
            result = terminal_state(solve_source(source))
            assert result.get_var('result').get_int_lattice().get_consts() == frozenset({10 if expected else 20})


def test_finite_uncertainty_preserves_all_results():
    state=AIState().set_var('x',AbsVal.from_ints(frozenset({-3,4})))
    value=terminal_state(solve_source('y=x*2+1',initial_state=state)).get_var('y')
    assert int_contains(value,-5) and int_contains(value,9)


def test_computed_scalar_crosses_return_boundary(tmp_path):
    with analyze_program(tmp_path,'def f():\n x=2\n return x*3\nz=f()') as run:
        result=run.ai.get_summary('subject.f')
        assert 6 in {obj.value for obj in result.ret if isinstance(obj,ConstantObject)}


def test_unbounded_operation_retains_open_obligation():
    solver=solve_source('y=x+1',initial_state=AIState().set_var('x',AbsVal.top()))
    assert 'unsupported_or_unbounded_scalar_operation' in solver.summary.incomplete_reasons
