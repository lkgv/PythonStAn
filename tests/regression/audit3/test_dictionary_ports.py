"""The actual dictionary producer separates handler identity from display text."""

from pythonstan.analysis.contracts.identity import CellId, EntryScenario, SourceModule
from pythonstan.analysis.contracts.json_data import require_array, require_object
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver


def test_real_dictionary_relation_uses_function_identity_and_preserves_display() -> None:
    source = SourceModule(
        "example",
        "example.py",
        "def first():\n    return 'first'\n"
        "def second():\n    return 'second'\n"
        "table = {'read': first, 'write': second}\n"
        "chosen = table['read']\nresult = chosen()\n",
    )
    driver = InterproceduralDriver((source,), options={"resources": {"max_seconds": 0}})
    snapshot = driver.run((EntryScenario("example"),)).to_dict()
    function_id = next(
        identifier
        for identifier, info in driver.functions.items()
        if info.qualname == "example.first"
    )
    exits = [
        flow
        for unit in driver.units.values()
        if unit.is_module
        for flow in unit.outcomes
        if flow.kind == "normal"
    ]
    assert exits
    for flow in exits:
        chosen = flow.store.cells[CellId("global:example:chosen")]
        assert chosen.refs == frozenset({function_id})
        assert chosen.relations
        for token in chosen.relations:
            assert token.handler == function_id
            assert token.handler in driver.functions
            assert token.display_name == "example.first"
    values = require_object(require_object(snapshot["values"])["example"])
    relations = require_array(require_object(values["chosen"])["relations"])
    assert relations
    assert all(require_object(relation)["handler"] == "example.first" for relation in relations)
