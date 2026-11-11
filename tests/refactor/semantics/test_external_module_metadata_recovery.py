"""Absent external source metadata is an open boundary, not an internal error."""
import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.state import Frame, HeapObject, Store


def exact(result, name, expected):
    answer = result.query(ValueQuery("subject", name))
    assert answer.status == "answered", answer.to_dict()
    value = answer.value
    assert value["tagged_constants"] == [{"type": type(expected).__name__, "value": expected}], value
    assert value["constants"] == [expected] and not value["objects"], value
    assert not (value["top"] or value["top_sorts"] or value["may_unbound"] or value["bottom"]), value


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
@pytest.mark.parametrize("expression", [
    "importlib.util", "importlib.__dict__", "importlib.__spec__",
    "vars(importlib)", "importlib.__dict__['__name__']",
])
def test_external_parent_module_metadata_keeps_open_effects_and_useful_continuation(engine, expression):
    # A dotted unresolved import creates the parent module identity in the
    # normal may-import arm. No target module is imported by the analyzer.
    source = "import importlib.util\nvalue=" + expression + "\nresult='after'\n"
    queries = (ValueQuery("subject", "value"), ValueQuery("subject", "result"), "effects", "calls")
    result = analyze(Source(source, module="subject"),
                     config={"engine": engine, "resources": {"max_work": 50000, "max_seconds": 0}},
                     queries=queries)
    snapshot = result.raw_snapshot()
    exact(result, "result", "after")
    uncertain = result.query(ValueQuery("subject", "value"))
    assert uncertain.status == "answered" and uncertain.value["top"], uncertain.to_dict()
    assert result.solver_status == "converged" and result.coverage_status == "open", snapshot
    assert any(row["code"].startswith("external_module_attribute:importlib.") for row in snapshot["obligations"]), snapshot["obligations"]
    assert snapshot["effects"]["havoc"] and "BaseException" in snapshot["effects"]["exceptions"], snapshot["effects"]
    assert any(call["callee"] == "<callback>" for call in snapshot["calls"]), snapshot["calls"]


def test_direct_external_namespace_view_has_real_unknown_read_effects():
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", "pass\n"),),
                                   {"resources": {"max_work": 50000, "max_seconds": 0}})
    node = driver.trees["subject"].body[0]
    frame = Frame("subject", "subject", (), "test:subject", is_module=True)
    store = Store(cells={"global:subject:marker": Value.constant("before")},
                  heap={"box": HeapObject("dict", {"value": Value.constant("before")})})
    view = driver.transfer.namespaces.global_view("opaque_external", store)
    assert "opaque_external" in store.havoc_namespaces
    assert any(reason.startswith("external_module_namespace:opaque_external") for reason in store.obligations)
    flows = driver.transfer.namespaces.getitem(next(iter(view.refs)), Value.constant("__name__"), store, frame, node)
    normal = [flow for flow in flows if flow.kind == "normal"]
    assert normal and all(flow.value.top and flow.store.heap["box"].havoc for flow in normal)
    assert all(flow.store.effective_cell("global:subject:marker").top for flow in normal)
    assert any(flow.kind == "raise" and flow.exception == "AnyException" for flow in flows)


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
def test_inventoried_source_metadata_and_deleted_doc_remain_precise(engine):
    source = "'source doc'\nname=__name__\nview=globals()\ndel __doc__\ntry:\n value=view['__doc__']\nexcept KeyError:\n result=name+'-deleted'\n"
    result = analyze(Source(source, module="subject"), config={"engine": engine},
                     queries=(ValueQuery("subject", "result"),))
    exact(result, "result", "subject-deleted")
    assert result.coverage_status == "closed_under_profile", result.raw_snapshot()["obligations"]
