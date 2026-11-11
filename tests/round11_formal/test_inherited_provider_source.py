"""Concrete-result obligation for conditional providers under both schedules."""
import pytest

from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.world import World
from tests.pointer.kcfa.test_ai_bridge import analyze_source


SOURCE = '''def earlier(self): return "earlier"
def later(self): return "later"
def construct(flag):
    class First: pass
    class Second:
        run = later
    class Derived(First, Second): pass
    if flag:
        First.run = earlier
    return Derived()
first = construct(True).run()
second = construct(False).run()
'''


@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
def test_conditional_earlier_may_provider_does_not_remove_required_later_result(
        tmp_path, policy, context_policy):
    concrete = {}; exec(SOURCE, concrete)
    assert concrete["first"] == "earlier" and concrete["second"] == "later"
    query, edges = analyze_source(tmp_path, SOURCE, work_scheduling_policy=policy,
        context_policy=context_policy, enable_synthetic_method_contexts=False,
        enable_ai_intrinsics=False)
    for name in ("first", "second"):
        values = {obj.value for obj in query.points_to_at(name, World().get_entry_module())
                  if isinstance(obj, ConstantObject)}
        assert concrete[name] in values, (name, values)
    assert {("subject", "subject.earlier"), ("subject", "subject.later")} <= edges
    assert query.get_statistics()["converged"] == 1
