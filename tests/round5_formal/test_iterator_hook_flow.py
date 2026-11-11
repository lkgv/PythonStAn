"""Known custom iterator hooks retain callbacks through type-only lookup."""
from tests.pointer.kcfa.test_ai_bridge import analyze_source
from tests.round5_formal.test_heap_views import schedule


def test_nested_iter_next_preserves_custom_iterator_callback(tmp_path, schedule):
    query, edges = analyze_source(tmp_path, '''
def callback(): return 19
class Cursor:
    def __iter__(self): return self
    def __next__(self): return callback
class Collection:
    def __iter__(self): return Cursor()
selected = next(iter(Collection()))
result = selected()
''', enable_ai_intrinsics=False, track_unknowns=False)
    assert ("subject", "subject.Collection.__iter__") in edges
    assert ("subject", "subject.Cursor.__next__") in edges
    assert ("subject", "subject.callback") in edges
    assert query.get_statistics()["converged"] == 1
    assert query.get_residual_effects()


def test_instance_shadow_cannot_replace_implicit_iterator_hooks(tmp_path, schedule):
    query, edges = analyze_source(tmp_path, '''
def callback(): return 19
def shadow(): return 99
class Cursor:
    def __iter__(self): return self
    def __next__(self): return callback
cursor = Cursor()
cursor.__iter__ = shadow
cursor.__next__ = shadow
selected = next(iter(cursor))
selected()
''', enable_ai_intrinsics=False)
    assert ("subject", "subject.callback") in edges
    assert not any(callee == "subject.shadow" for _, callee in edges)
    assert query.get_statistics()["converged"] == 1


def test_late_class_function_patch_keeps_receiver_and_return_flow(tmp_path, schedule):
    query, edges = analyze_source(tmp_path, '''
def callback(): return 19
def iterate(self): return self
def advance(self): return self.callback
class Cursor:
    pass
Cursor.__iter__ = iterate
Cursor.__next__ = advance
cursor = Cursor()
cursor.callback = callback
selected = next(iter(cursor))
selected()
''', enable_ai_intrinsics=False)
    assert {("subject", "subject.iterate"), ("subject", "subject.advance"),
            ("subject", "subject.callback")} <= edges
    assert query.get_statistics()["converged"] == 1


def test_discarded_iterator_results_still_observe_hook_effects(tmp_path, schedule):
    query, edges = analyze_source(tmp_path, '''
def on_iter(): return 1
def on_next(): return 2
class Cursor:
    def __iter__(self):
        on_iter()
        return self
    def __next__(self):
        on_next()
        return None
cursor = Cursor()
iter(cursor)
next(cursor)
''', enable_ai_intrinsics=False, track_unknowns=False)
    assert ("subject.Cursor.__iter__", "subject.on_iter") in edges
    assert ("subject.Cursor.__next__", "subject.on_next") in edges
    assert query.get_statistics()["converged"] == 1
