"""The actual Native solver configuration owns checked JSON at its boundary."""

from dataclasses import fields

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject, require_array, require_object
from pythonstan.analysis.pointer.kcfa.config import Config


def test_actual_native_config_json_roundtrip_covers_the_single_record() -> None:
    config = Config.from_dict(
        {
            "context_policy": "1-obj",
            "heap_context_depth": 0,
            "max_iterations": 1,
            "entry_points": [],
            "enable_synthetic_method_contexts": False,
            "work_scheduling_policy": "fair",
            "ai_budget": {"max_iterations": 0},
        }
    )
    exported = config.to_dict()
    assert set(exported) == {option.name for option in fields(Config)}
    assert Config.from_dict(exported) == config
    assert config.entry_points == []
    assert config.max_iterations == 1
    assert config.ai_budget == {"max_iterations": 0}
    assert config.enable_synthetic_method_contexts is False


def test_native_config_input_and_export_do_not_share_mutable_json() -> None:
    raw: JsonObject = {"entry_points": ["main"], "ai_budget": {"max_iterations": 0}}
    config = Config.from_dict(raw)
    require_array(raw["entry_points"]).append("input-mutated")
    require_object(raw["ai_budget"])["max_iterations"] = 3
    assert config.entry_points == ["main"]
    assert config.ai_budget == {"max_iterations": 0}
    exported = config.to_dict()
    require_array(exported["entry_points"]).append("export-mutated")
    require_object(exported["ai_budget"])["max_iterations"] = 7
    assert config.entry_points == ["main"]
    assert config.ai_budget == {"max_iterations": 0}


def test_driver_only_fields_are_filtered_without_becoming_solver_options() -> None:
    config = Config.from_dict(
        {"test_root": "project", "test_dir": "tests", "test_files": ["one.py"]}
    )
    assert config.to_dict() == Config().to_dict()
    with pytest.raises(ValueError, match="Unknown KCFA options"):
        Config.from_dict({"invented_solver_control": True})


@pytest.mark.parametrize(
    "raw",
    [
        {"max_iterations": True},
        {"heap_context_depth": False},
        {"entry_points": [""]},
        {"project_path": 7},
        {"debug_output_dir": False},
        {"ai_budget": {"max_iterations": True}},
        {"ai_budget": {"max_const_strings": 0}},
        {"library_paths": ["unsupported"]},
    ],
)
def test_native_codec_rejects_wrong_types_and_retains_supported_control_limits(
    raw: JsonObject,
) -> None:
    with pytest.raises(ValueError):
        Config.from_dict(raw)
