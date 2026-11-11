"""Configuration and actual pass execution must describe the same pipeline."""

from collections import Counter
from types import SimpleNamespace

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.world import World
from pythonstan.world.analysis_manager import AnalysisManager
from pythonstan.world.config import Config
from pythonstan.world.pipeline import Pipeline


def config(name, dependencies=()):
    return AnalysisConfig(name, "PointerAnalysis", prev_analysis=list(dependencies),
                          options={"type": "pointer analysis"})


def test_minimal_pipeline_configuration_runs_real_source_pass(tmp_path):
    source = tmp_path / "entry.py"
    source.write_text("def entry():\n    return None\n")
    pipeline = Pipeline(config={
        "filename": str(source),
        "analysis": [{"name": "checked", "id": "SourceCertificateDriver",
                      "options": {"type": "certified analysis"}}],
    })
    pipeline.run()
    assert pipeline.config.project_path == str(tmp_path)
    assert pipeline.config.library_paths == []
    assert pipeline.analysis_manager.get_results("checked").certified
    assert World().scope_manager.get_ir(World().get_entry_module(), "cfg") is not None


def test_config_object_and_preprocessing_only_defaults(tmp_path):
    source = tmp_path / "entry.py"
    source.write_text("value = 1\n")
    pipeline = Pipeline(config=Config(source))
    pipeline.run()
    assert set(pipeline.analysis_manager.results) == {
        "three address", "ir", "block cfg", "cfg", "closure",
    }


@pytest.mark.parametrize("options", [None, [], {}, {"type": []}, {"type": "unknown"}])
def test_invalid_analysis_options_are_explicit(options):
    with pytest.raises(ValueError):
        AnalysisConfig("example", "PointerAnalysis", options=options)


@pytest.mark.parametrize("changes", [
    {"typo": True}, {"analysis": "not-a-list"}, {"library_paths": "src"},
    {"time_count": "false"}, {"import_level": True}, {"import_level": -2},
    {"analysis": [{"name": "x", "id": "PointerAnalysis", "typo": 1}]},
])
def test_invalid_pipeline_options_are_explicit(changes):
    with pytest.raises(ValueError):
        Config.from_dict({"filename": "entry.py", **changes})


@pytest.mark.parametrize("dependencies", ["cfg", ["cfg", "cfg"], [None]])
def test_invalid_dependency_shape_is_rejected(dependencies):
    with pytest.raises(ValueError):
        AnalysisConfig("example", "PointerAnalysis", prev_analysis=dependencies,
                       options={"type": "pointer analysis"})


def test_configuration_snapshots_input_lists_and_options():
    dependencies = ["cfg"]
    options = {"type": "pointer analysis"}
    supplied = AnalysisConfig("example", "PointerAnalysis", prev_analysis=dependencies,
                              options=options)
    dependencies.append("missing")
    options["type"] = "unknown"
    assert supplied.prev_analysis == ["cfg"]
    assert supplied.type == supplied.options["type"] == "pointer analysis"


class RecordingDriver:
    def __init__(self, supplied, visits):
        self.config = supplied
        self.visits = visits
        self.results = None

    def analyze(self, scope, previous):
        self.visits.append((self.config.name, set(previous)))
        self.results = self.config.name


@pytest.mark.parametrize("configs,expected", [
    ([config("A"), config("B"), config("C", ["A", "D"]), config("D", ["B"])],
     ["A", "B", "D", "C"]),
    ([config("A"), config("B", ["A"]), config("C", ["A"]), config("D", ["B", "C"])],
     ["A", "B", "C", "D"]),
])
def test_each_pass_waits_for_all_predecessor_results_once(monkeypatch, configs, expected):
    visits = []
    original = AnalysisManager.gen_analyzer
    names = {item.name for item in configs}
    monkeypatch.setattr(AnalysisManager, "gen_analyzer",
                        lambda self, item: RecordingDriver(item, visits)
                        if item.name in names else original(self, item))
    manager = AnalysisManager()
    manager.build(configs)
    for driver in manager.generator():
        manager.do_analysis(driver, None)
    assert [name for name, _ in visits] == expected
    assert len(visits) == len(configs)
    for name, previous in visits:
        assert previous == set(manager.analyzers[name].config.prev_analysis)


@pytest.mark.parametrize("configs,match", [
    ([config("A", ["missing"])], "Unknown dependency"),
    ([config("A", ["B"]), config("B", ["A"])], "Cyclic"),
    ([config("A", ["A"])], "Cyclic"),
    ([config("A"), config("A")], "Duplicate"),
    ([config("cfg")], "Duplicate"),
])
def test_invalid_graph_fails_before_constructing_any_driver(monkeypatch, configs, match):
    constructed = []
    monkeypatch.setattr(AnalysisManager, "gen_analyzer",
                        lambda self, item: constructed.append(item))
    with pytest.raises(ValueError, match=match):
        AnalysisManager().build(configs)
    assert not constructed


def test_custom_closure_configuration_is_executed_once(tmp_path, monkeypatch):
    from pythonstan.analysis.closure import ClosureAnalysis
    source = tmp_path / "entry.py"
    source.write_text("def outer():\n    value = 1\n    def inner():\n        return value\n    return inner\n")
    calls = []
    original = ClosureAnalysis.analyze

    def observe(self, scope, previous):
        calls.append(self.config.name)
        return original(self, scope, previous)

    monkeypatch.setattr(ClosureAnalysis, "analyze", observe)
    pipeline = Pipeline(config={
        "filename": str(source),
        "analysis": [{"name": "custom-closure", "id": "ClosureAnalysis",
                      "prev_analysis": ["cfg"],
                      "options": {"type": "closure analysis"}}],
    })
    pipeline.run()
    assert calls == ["closure", "custom-closure"]
    assert "custom-closure" in pipeline.analysis_manager.results


@pytest.mark.parametrize("graph", [
    {"entry": "import left\nimport right\n", "left": "import shared\n",
     "right": "import shared\n", "shared": "value = 1\n"},
    {"entry": "import second\n", "second": "import entry\n"},
])
def test_module_passes_cover_diamonds_and_cycles_once(tmp_path, monkeypatch, graph):
    for name, source in graph.items():
        (tmp_path / f"{name}.py").write_text(source)
    pipeline = Pipeline(config={"filename": str(tmp_path / "entry.py"), "mock_libs": False})
    visits = []
    monkeypatch.setattr(pipeline.analysis_manager, "do_analysis",
                        lambda analyzer, module: visits.append(module.get_qualname()))
    for kind in ("transform", "dataflow analysis"):
        visits.clear()
        driver = SimpleNamespace(config=SimpleNamespace(type=kind, inter_procedure=False))
        pipeline.do_analysis(iter([driver]))
        assert len(visits) == len(graph)
        assert set(Counter(visits).values()) == {1}


def test_lazy_imports_are_registered_but_not_run_without_ir(tmp_path, monkeypatch):
    (tmp_path / "entry.py").write_text("import imported\n")
    (tmp_path / "imported.py").write_text("value = 1\n")
    pipeline = Pipeline(config={"filename": str(tmp_path / "entry.py"),
                                "mock_libs": False, "lazy_ir_construction": True})
    assert len(World().scope_manager.get_module_graph().get_modules()) == 2
    visited = []
    monkeypatch.setattr(pipeline.analysis_manager, "do_analysis",
                        lambda analyzer, module: visited.append(module))
    pipeline.do_transform(object())
    assert visited == [World().get_entry_module()]


def test_mutated_unsupported_dispatch_is_not_silently_skipped():
    pipeline = Pipeline.__new__(Pipeline)
    bad = SimpleNamespace(config=SimpleNamespace(type="unknown"))
    with pytest.raises(ValueError, match="Unsupported pipeline"):
        pipeline.do_analysis(iter([bad]))


@pytest.mark.parametrize("imported", [False, True])
def test_dataflow_dependencies_keep_each_modules_facts(tmp_path, monkeypatch, imported):
    from pythonstan.analysis.dataflow import ReachingDefinitionAnalysis
    from pythonstan.analysis.transform import Transform

    (tmp_path / "entry.py").write_text(
        ("import worker\n" if imported else "") + "entry_value = 1\nentry_copy = entry_value\n")
    if imported:
        (tmp_path / "worker.py").write_text("worker_value = 2\nworker_copy = worker_value\n")
    consumed = {}
    original = ReachingDefinitionAnalysis.set_input

    def record(self, name, value):
        consumed[(self.scope, name)] = value
        original(self, name, value)

    monkeypatch.setattr(ReachingDefinitionAnalysis, "set_input", record)

    class CaptureFlowFacts(Transform):
        def __init__(self, config):
            super().__init__(config)

        def transform(self, module):
            self.results = dict(self.inputs)

    pipeline = Pipeline({
        "filename": str(tmp_path / "entry.py"), "mock_libs": False,
        "analysis": [
            {"name": "live", "id": "LivenessAnalysis", "prev_analysis": ["cfg"],
             "options": {"type": "dataflow analysis"}},
            {"name": "defs", "id": "ReachingDefinitionAnalysis", "prev_analysis": ["cfg", "live"],
             "options": {"type": "dataflow analysis"}},
            {"name": "capture", "id": "CaptureFlowFacts", "prev_analysis": ["live", "defs"],
             "options": {"type": "transform"}},
        ],
    })
    pipeline.run()
    live = pipeline.analysis_manager.get_results("live")
    defs = pipeline.analysis_manager.get_results("defs")
    captured = pipeline.analysis_manager.get_results("capture")
    modules = list(pipeline._modules_once())
    assert len(modules) == (2 if imported else 1)
    assert set(live["per_scope"]) == set(defs["per_scope"]) == set(modules)
    for module in modules:
        assert consumed[(module, "live")] is live["per_scope"][module]
        assert captured[module]["live"] is live["per_scope"][module]
        assert captured[module]["defs"] is defs["per_scope"][module]
        assert live["per_scope"][module]["in"]
    # The old flat view remains available; it describes the last analyzed scope.
    assert live["in"] is live["per_scope"][modules[-1]]["in"]
    assert defs["out"] is defs["per_scope"][modules[-1]]["out"]
    if imported:
        assert set(live["per_scope"][modules[0]]["in"]).isdisjoint(
            live["per_scope"][modules[1]]["in"])


def test_scope_result_legacy_view_and_missing_module_error():
    from pythonstan.analysis.analysis import get_scope_result
    scope, other = object(), object()
    facts = {"in": {"block": 1}, "out": {"block": 2}}
    assert get_scope_result(facts, scope) == facts
    assert get_scope_result({scope: facts}, scope) is facts
    # Never use the latest module's compatibility aliases for a missing module.
    with pytest.raises(ValueError, match="has no result for scope"):
        get_scope_result({**facts, "per_scope": {other: facts}}, scope, "live")
    with pytest.raises(ValueError, match="does not expose per-scope"):
        get_scope_result(None, scope, "missing")
