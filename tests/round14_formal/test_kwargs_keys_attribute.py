"""Independent ordinary-keys vs special-getitem expansion obligation."""
import hashlib
from pathlib import Path

import pytest

from tests.round12_semantics.test_continuations import analyze, edges, event_values, values


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_keyword_expansion_honors_instance_keys_attribute_and_callback_value(tmp_path, policy):
    from pythonstan.world import World

    raw = Path(__file__).with_name("kwargs_instance_keys_source.py.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "b34b7f4145a1f39ff20ebee2d7cbbd17d55d1d093f17200855f62b9d32dda69b"
    source = raw.decode()
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 7
    assert concrete["events"] == ["instance-keys", "getitem", "chosen"]
    World.setup()
    data = analyze(tmp_path, source, policy=policy)
    assert {("<module>", "keys_hook"), ("<module>", "RequestMapping.__getitem__"),
            ("consume", "chosen")} <= edges(data)
    assert {"instance-keys", "getitem", "chosen"} <= event_values(data)
    entities = {row["id"]: row for row in data["entities"]}
    represented = {entities[object_id]["attributes"]["value"] for object_id in values(data, "result")
                   if "value" in entities[object_id]["attributes"]}
    assert 7 in represented
