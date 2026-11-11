"""Independent finite CPython oracles for ordinary live dictionary views.

Only these authored bounded fixtures run in the test-only child. Analysis uses
the public Source entry and does not consume the concrete output as an input.
"""
import pytest

from pythonstan import Source, ValueQuery, analyze
from scripts.refactor.semantic_oracle import concrete_witness


def check(source, expected):
    witness = concrete_witness({"id": "finite_live_dict_view", "source": source})
    observation = witness["observation"]
    assert observation["normal_exit"] and observation["bound"], witness
    assert observation["value"] == {
        "kind": "scalar", "type": type(expected).__name__, "value": expected,
    }, witness
    query = ValueQuery("subject", "result")
    result = analyze(
        Source(source, filename="live_views.py", module="subject"),
        config={"engine": "ai_primary", "resources": {"max_work": 50000, "max_seconds": 0}},
        queries=(query, "effects", "calls"),
    )
    answer = result.query(query)
    assert answer.status == "answered", answer.to_dict()
    value = answer.value
    assert value["constants"] == [expected], value
    assert value["tagged_constants"] == [{"type": type(expected).__name__, "value": expected}], value
    assert value["types"] == [type(expected).__name__] and not value["objects"], value
    assert not (value["top"] or value["top_sorts"] or value["may_unbound"] or value["bottom"]), value
    assert result.solver_status == "converged", result.raw_snapshot()
    assert result.coverage_status == "closed_under_profile", result.raw_snapshot()["obligations"]
    return result


@pytest.mark.parametrize("method", ["keys", "values", "items"])
def test_view_length_follows_owner_mutation(method):
    check(f"d={{'x':1}}\nview=d.{method}()\nd['y']=2\nresult=len(view)\n", 2)


@pytest.mark.parametrize(("method", "projection", "expected"), [
    ("keys", "collected[0]+collected[1]", "xy"),
    ("values", "collected[0]*10+collected[1]", 12),
    ("items", "collected[0][0]+str(collected[0][1])+collected[1][0]+str(collected[1][1])", "x1y2"),
])
def test_view_materialization_retains_useful_finite_owner_entries(method, projection, expected):
    check(f"d={{'x':1}}\nview=d.{method}()\nd['y']=2\ncollected=list(view)\nresult={projection}\n", expected)


@pytest.mark.parametrize(("method", "needle"), [
    ("keys", "'y'"), ("values", "2"), ("items", "('y',2)"),
])
def test_view_membership_sees_new_entries(method, needle):
    check(f"d={{'x':1}}\nview=d.{method}()\nd['y']=2\nresult={needle} in view\n", True)


@pytest.mark.parametrize("method", ["keys", "values", "items"])
def test_views_are_not_subscriptable(method):
    check(f"d={{'x':1}}\nview=d.{method}()\ntry:\n result=view[0]\nexcept TypeError:\n result='not-subscriptable'\n", "not-subscriptable")


@pytest.mark.parametrize("method", ["keys", "values", "items"])
def test_size_mutation_during_active_view_iteration_raises_runtimeerror(method):
    check(f"d={{'x':1,'y':2}}\nview=d.{method}()\nit=iter(view)\nfirst=next(it)\nd['z']=3\ntry:\n next(it)\n result='missed-change'\nexcept RuntimeError:\n result='size-changed'\n", "size-changed")


@pytest.mark.parametrize(("method", "projection"), [
    ("values", "first*10+second"),
    ("items", "first[1]*10+second[1]"),
])
def test_value_replacement_during_iteration_is_live_without_size_error(method, projection):
    check(f"d={{'x':1,'y':2}}\nview=d.{method}()\nit=iter(view)\nfirst=next(it)\nd['y']=9\nsecond=next(it)\nresult={projection}\n", 19)


@pytest.mark.parametrize(("method", "needle"), [
    ("values", "1"), ("items", "('x',1)"),
])
def test_replaced_values_do_not_remain_as_stale_members(method, needle):
    check(f"d={{'x':1}}\nview=d.{method}()\nd['x']=9\nresult={needle} in view\n", False)


@pytest.mark.parametrize("name", ["__class__", "__repr__"])
def test_existing_object_attributes_do_not_become_closed_attributeerror(name):
    source = f"view={{'x':1}}.keys()\ntry:\n candidate=view.{name}\n result='present'\nexcept AttributeError:\n result='absent'\n"
    witness = concrete_witness({"id": "finite_view_existing_attribute", "source": source})
    assert witness["observation"]["value"]["value"] == "present", witness
    query = ValueQuery("subject", "result")
    result = analyze(Source(source, module="subject"), config={"engine": "ai_primary"}, queries=(query,))
    answer = result.query(query)
    assert answer.status == "answered" and "present" in answer.value["constants"], answer.to_dict()
    # A conservative runtime-slot residual is permitted, but a false closed
    # definite AttributeError is not. Ordinary view operations above stay exact.
    if answer.value["constants"] != ["present"]:
        assert result.coverage_status == "open", result.raw_snapshot()
