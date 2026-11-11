"""Concrete differential checks of lowering, independent of abstract values."""
import ast
import math
import pytest
from pythonstan.analysis.transform.three_address import ThreeAddressTransformer, ConstCollector
from tests.analysis.legacy_ai_support import build_source


def execute_lowered(source):
    ordinary, lowered = {}, {}
    exec(compile(source, '<oracle>', 'exec'), ordinary)
    tree = ThreeAddressTransformer().visit(ast.parse(source))
    tree = ast.fix_missing_locations(tree)
    exec(compile(tree, '<lowered>', 'exec'), lowered)
    return ordinary, lowered


@pytest.mark.parametrize('source', [
    'i=0\nwhile i<4:\n i+=1\nresult=i',
    'i=0\ntotal=0\nwhile i<4:\n i+=1\n if i<3: continue\n total+=i\nelse:\n total+=100\nresult=total',
    'i=0\nwhile i<4:\n i+=1\n if i==2: break\nelse:\n i=100\nresult=i',
    'i=0\nx=0\nwhile i<4:\n i+=1\n while False:\n  x=100\n else:\n  if i<3: continue\n x+=1\nresult=x',
    'i=0\nwhile i<4:\n i+=1\n while False:\n  i=100\n else:\n  break\nresult=i',
])
def test_while_guard_continue_break_and_else_match_python(source):
    ordinary, lowered = execute_lowered(source)
    assert lowered['result'] == ordinary['result']


def test_constant_collector_preserves_exact_runtime_type_and_float_bits():
    collector = ConstCollector()
    values = [True, 1, 1.0, False, 0, 0.0, -0.0, 1+0j, '1', b'1', None]
    names = [collector.load(value).id for value in values]
    assert len(set(names)) == len(values)
    restored = dict(collector.dump())
    for name, value in zip(names, values):
        assert type(restored[name]) is type(value)
        assert restored[name] == value
    assert math.copysign(1,restored[names[5]]) == 1
    assert math.copysign(1,restored[names[6]]) == -1
