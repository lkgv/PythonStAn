"""Implicit truth testing must retain type-level callback effects."""
import pytest

from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.mark.parametrize("method,result", [("__bool__", "False"), ("__len__", "0")])
@pytest.mark.parametrize("ai", [False, True])
def test_truth_hook_and_discarded_result_effect(tmp_path, method, result, ai):
    source = f'''
def callback(): return 5
class Flag:
    def __init__(self, fn): self.fn = fn
    def {method}(self):
        self.fn()
        return {result}
def invoke(flag):
    if flag: pass
invoke(Flag(callback))
'''
    observed = []
    concrete = {}
    exec(source.replace("def callback(): return 5", "def callback(): observed.append(5)"), {"observed": observed})
    assert observed == [5]
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=ai)
    assert ("subject.invoke", "subject.Flag." + method) in edges
    assert ("subject.Flag." + method, "subject.callback") in edges
    assert not query.get_semantic_status()["certificate_eligible"]


def test_instance_hook_shadow_is_ignored(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def shadow(): return True
class Flag:
    def __bool__(self): return False
flag = Flag()
flag.__bool__ = shadow
def invoke(value):
    if value: pass
invoke(flag)
''')
    assert ("subject.invoke", "subject.Flag.__bool__") in edges
    assert ("subject.invoke", "subject.shadow") not in edges


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_late_type_hook_assignment_replays_truth_test(tmp_path, policy):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 7
class Flag: pass
def truth(self):
    callback()
    return True
def invoke(value):
    if value: pass
flag = Flag()
Flag.__bool__ = truth
invoke(flag)
''', context_policy=policy)
    assert ("subject.invoke", "subject.truth") in edges
    assert ("subject.truth", "subject.callback") in edges


def test_known_presence_does_not_unsafely_prune_len_may_candidate(tmp_path):
    query, edges = analyze_source(tmp_path, '''
class Flag:
    def __bool__(self): return False
    def __len__(self): return 1
def invoke(value):
    if value: pass
invoke(Flag())
''')
    assert ("subject.invoke", "subject.Flag.__bool__") in edges
    assert ("subject.invoke", "subject.Flag.__len__") in edges
    assert any("truth_protocol" in entry.get("effects", ())
               for entry in query.get_residual_effects())
