"""Only operational public controls may accept a nondefault override."""
import pytest
from pythonstan.config import ConfigurationError, catalog, resolve_config

@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
@pytest.mark.parametrize("option", [
    {"andersen":{"context_policy":"3-obj"}},
    {"andersen":{"index_sensitive":False}},
    {"andersen":{"heap_context_depth":1}},
    {"abstract":{"max_recursion_depth":9}},
    {"abstract":{"max_const_ints":99}},
    {"abstract":{"solver_options":{"enable_trace_partitioning":True}}},
])
def test_inactive_nondefault_rejected(engine, option):
    with pytest.raises(ConfigurationError): resolve_config(config={"engine":engine, **option})

@pytest.mark.parametrize("name,value", [("entry_points",["x.f"]),("project_path","/tmp/project"),("enable_debug_monitor",True),("track_object_flow",True)])
def test_overwritten_or_unavailable_native_control_rejected(name,value):
    with pytest.raises(ConfigurationError): resolve_config(config={"andersen":{name:value}})

def test_native_controls_and_new_core_caps_remain_operational():
    config=resolve_config(config={"engine":"native_then_ai","andersen":{"context_policy":"3-obj","index_sensitive":False},"abstract":{"context_depth":3,"max_range_size":5,"max_scalar_pairs":8}})
    assert config["andersen"]["context_policy"]=="3-obj"
    assert config["abstract"]["context_depth"]==3
    assert resolve_config()["andersen"]["enable_synthetic_method_contexts"] is False
    rows={row["path"]:row for row in catalog()}
    assert rows["andersen.context_policy"]["engines"]==("native","native_then_ai")
    assert rows["abstract.max_const_ints"]["engines"]==()

def test_relations_alias_changes_actual_partition_switch_and_conflict_rejected():
    assert resolve_config(config={"engine":"ai_primary","abstract":{"relations":False}})["abstract"]["enable_value_partition"] is False
    assert resolve_config(config={"engine":"ai_primary","abstract":{"enable_value_partition":False}})["abstract"]["relations"] is False
    with pytest.raises(ConfigurationError): resolve_config(config={"engine":"ai_primary","abstract":{"relations":False,"enable_value_partition":True}})

def test_precise_preset_uses_new_integer_domain_control():
    config=resolve_config(config={"engine":"ai_primary","preset":"precise"})
    assert config["abstract"]["int_domain_max_consts"]==20
