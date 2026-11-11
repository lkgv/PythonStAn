"""Iterator extension regression boundaries beyond the independent formal suite."""
from dataclasses import replace

import pytest

from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
from tests.pointer.kcfa.test_ai_bridge import analyze_source
from tests.round5_formal.test_heap_views import schedule


def test_targetless_iterator_calls_keep_hooks(tmp_path, monkeypatch, schedule):
    translate = IRTranslator._translate_call

    def targetless(self, statement):
        constraints = translate(self, statement)
        return [replace(c, target=None) if isinstance(c, CallConstraint)
                and c.callee.name in {"iter", "next"} else c for c in constraints]

    monkeypatch.setattr(IRTranslator, "_translate_call", targetless)
    _, edges = analyze_source(tmp_path, '''
def start(): return 1
def advance(): return 2
class Cursor:
    def __iter__(self):
        start()
        return self
    def __next__(self):
        advance()
        return None
cursor = Cursor()
iter(cursor)
next(cursor)
''', enable_ai_intrinsics=False)
    assert ("subject.Cursor.__iter__", "subject.start") in edges
    assert ("subject.Cursor.__next__", "subject.advance") in edges


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_builtin_containers_do_not_get_empty_instance_hook_callees(tmp_path, schedule, index_sensitive):
    query, edges = analyze_source(tmp_path, '''
def chosen(): return 7
callback = next(iter([chosen]))
callback()
''', index_sensitive=index_sensitive, enable_ai_intrinsics=False)
    assert ("subject", "subject.chosen") in edges
    assert not any("$iter_method_" in str(record) or "$next_method_" in str(record)
                   for record in query.get_unknown_details())


def test_inherited_iterator_hooks_keep_receiver_fields(tmp_path, schedule):
    _, edges = analyze_source(tmp_path, '''
def chosen(): return 7
class Base:
    def __iter__(self): return self
    def __next__(self): return self.callback
class Child(Base): pass
cursor = Child()
cursor.callback = chosen
next(iter(cursor))()
''', enable_ai_intrinsics=False)
    assert ("subject", "subject.chosen") in edges
