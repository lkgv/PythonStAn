"""Real AST -> TAC -> IR -> CFG -> PTA/AI interoperability regressions.

The old tests only constructed mocks or asserted that method names existed.
Every retained historical test now observes a production transfer or graph.
"""
import pytest
from pythonstan.analysis.pointer.ai import AIState, AbsVal
from tests.analysis.legacy_ai_support import analyze_program, solve_source, terminal_state, int_contains


def scalar(source, initial=None):
    solver = solve_source(source, initial_state=initial)
    assert solver.cfg_adapter is not None
    return solver, terminal_state(solver)


def require_edges(run, *edges):
    assert set(edges) <= run.edges
    assert run.ai.summaries


class TestBasicCFGConsistency:
    def test_simple_function_cfg_ai_consistency(self, tmp_path):
        with analyze_program(tmp_path, 'def simple_function(x):\n y=x+1\n return y\nz=simple_function(2)') as run:
            require_edges(run, ('subject', 'subject.simple_function'))
            assert run.ai.get_summary('subject.simple_function') is not None
        _, state = scalar('y=x+1', AIState().set_var('x', AbsVal.from_int(2)))
        assert int_contains(state.get_var('y'), 3)

    @pytest.mark.parametrize('n', [-3, 0, 4])
    def test_conditional_cfg_ai_consistency(self, n):
        solver, state = scalar('if x>0:\n result=x\nelse:\n result=-x', AIState().set_var('x', AbsVal.from_int(n)))
        assert int_contains(state.get_var('result'), abs(n))
        assert any(len(solver.cfg_adapter.get_successors(bid)) == 2 for bid in solver.cfg_adapter.get_all_block_ids())

    def test_loop_cfg_ai_consistency(self, tmp_path):
        source = 'def total(n):\n result=0\n for i in range(n):\n  result+=i\n return result\nz=total(4)'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject', 'subject.total'))
            assert run.ai.get_summary('subject.total') is not None
        _, state = scalar('i=0\nresult=0\nwhile i<4:\n result+=i\n i+=1')
        assert int_contains(state.get_var('result'), 6)


class TestInterprocedralCFGConsistency:
    def test_direct_function_call_consistency(self, tmp_path):
        with analyze_program(tmp_path, 'def callee(x): return x\ndef caller(x): return callee(x)\na=[]\nb=caller(a)') as run:
            require_edges(run, ('subject.caller','subject.callee'), ('subject','subject.caller'))
            assert run.query.may_alias('a','b',run.module)

    def test_indirect_function_call_consistency(self, tmp_path):
        source = 'def method_a(x): return x\ndef method_b(x): return x\nif unknown: fp=method_a\nelse: fp=method_b\na=[]\nb=fp(a)'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject','subject.method_a'), ('subject','subject.method_b'))
            assert run.query.may_alias('a','b',run.module)

    def test_virtual_method_call_consistency(self, tmp_path):
        source = 'class Base:\n def method(self): return self\nclass Derived(Base):\n def method(self): return self\na=Derived()\nb=a.method()'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject','subject.Derived.method'))
            assert run.query.may_alias('a','b',run.module)


class TestCFGEdgeConsistency:
    def test_control_flow_edge_consistency(self):
        _, state = scalar('x=0\nwhile x<3:\n if x<2: x=x+1\n else: x=x+2\ny=x')
        assert int_contains(state.get_var('y'), 4)

    def test_exception_edge_consistency(self, tmp_path):
        source = 'def risky(): raise ValueError()\ndef fallback(): return 1\ndef cleanup(): return 2\ntry:\n risky()\nexcept ValueError:\n result=fallback()\nfinally:\n cleanup()'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject','subject.risky'), ('subject','subject.fallback'), ('subject','subject.cleanup'))
            # Call coverage does not establish exact Python exception ordering.
            assert not run.query.get_semantic_status()['certificate_eligible']

    def test_call_edge_consistency(self, tmp_path):
        with analyze_program(tmp_path, 'def g(): return []\ndef f(): return g()\nx=f()') as run:
            require_edges(run, ('subject','subject.f'), ('subject.f','subject.g'))
            assert run.points('x')


class TestDataFlowConsistency:
    def test_variable_def_use_consistency(self):
        _, state = scalar('x=42\ny=x+1\nz=y*2')
        assert int_contains(state.get_var('x'),42)
        assert int_contains(state.get_var('y'),43)
        assert int_contains(state.get_var('z'),86)

    def test_object_field_consistency(self, tmp_path):
        with analyze_program(tmp_path, 'class Box: pass\nobj=Box()\nobj.field=42\nresult=obj.field') as run:
            assert 42 in run.constants('result')

    def test_interprocedural_dataflow_consistency(self, tmp_path):
        source = 'class Box: pass\ndef modify(param):\n param.field=42\n return param\na=Box()\nb=modify(a)\nresult=b.field'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject','subject.modify'))
            assert run.query.may_alias('a','b',run.module)
            assert 42 in run.constants('result')


class TestEndToEndScenarios:
    def test_simple_program_end_to_end(self, tmp_path):
        source = 'def add_one(x): return x+1\ndef main(): return add_one(4)\nresult=main()'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject','subject.main'), ('subject.main','subject.add_one'))
            assert run.ai.get_summary('subject.add_one') is not None

    def test_object_oriented_program_end_to_end(self, tmp_path):
        source = 'class Animal:\n def speak(self): return self\nclass Dog(Animal):\n def speak(self): return self\nclass Cat(Animal):\n def speak(self): return self\ndef speak_all(animals):\n for animal in animals:\n  animal.speak()\nspeak_all([Dog(),Cat()])'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject.speak_all','subject.Dog.speak'), ('subject.speak_all','subject.Cat.speak'))

    def test_error_handling_end_to_end(self, tmp_path):
        source = 'def risky(x):\n if x<0: raise ValueError()\n return x*2\ndef safe(values):\n results=[]\n for value in values:\n  try: results.append(risky(value))\n  except ValueError: results.append(0)\n return results\nresult=safe([-1,2])'
        with analyze_program(tmp_path, source) as run:
            require_edges(run, ('subject','subject.safe'), ('subject.safe','subject.risky'))
            assert run.points('result')
            assert not run.query.get_semantic_status()['certificate_eligible']
