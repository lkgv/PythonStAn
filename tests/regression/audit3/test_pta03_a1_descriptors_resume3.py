"""Known raw builtin descriptors retain Python callability and late target flows."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject, require_object

from .test_pta03_a1_resume2 import result_for

CASES = {
    "positional": "def ident(x):\n    return x\nwrapped=staticmethod(ident)\nresult=wrapped(17)\n",
    "keyword": "def ident(x):\n    return x\nwrapped=staticmethod(ident)\nresult=wrapped(x=17)\n",
    "late_wrapped_target": (
        "def ident(x):\n    return x\ndef produce():\n    return ident\n"
        "wrapped=staticmethod(produce())\nresult=wrapped(17)\n"
    ),
}


def _value(raw: JsonObject) -> JsonObject:
    return require_object(require_object(require_object(raw["values"])["subject"])["result"])


@pytest.mark.parametrize("source", CASES.values(), ids=CASES)
def test_raw_staticmethod_forwards_the_actual_callable(source: str) -> None:
    namespace: dict[str, object] = {}
    exec(compile(source, "<trusted-descriptor-fixture>", "exec"), namespace)
    assert namespace["result"] == 17
    raw = result_for(source, {}, ("result",))
    value = _value(raw)
    assert raw["solver_status"] == "converged"
    assert value["constants"] == [17] and not value["top"], raw


def test_raw_classmethod_has_no_normal_return_or_arbitrary_heap_havoc() -> None:
    source = "def ident(x):\n    return x\nwrapped=classmethod(ident)\nresult=wrapped(17)\n"
    with pytest.raises(TypeError):
        exec(compile(source, "<trusted-descriptor-fixture>", "exec"), {})
    raw = result_for(source, {}, ("result",))
    rows = require_object(require_object(raw["values"]).get("subject", {}))
    value = require_object(rows.get("result", {"constants": [], "top": False}))
    assert not value["constants"] and not value["top"], raw
    assert "classmethod object is not callable" in str(raw["obligations"])
