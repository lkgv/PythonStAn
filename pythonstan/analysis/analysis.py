from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping
from typing import Dict, Any, Type, Literal, List

from pythonstan.ir import IRScope


class AnalysisConfig:
    name: str
    id: str
    description: str
    type: Literal[
        "dataflow analysis",
        "transform",
        "pointer analysis",
        "closure analysis",
        "ai analysis",
        "certified analysis",
    ]
    inter_procedure: bool
    prev_analysis: List[str]  # previous analysis name
    options: Dict[str, Any]

    def __init__(
        self, name, id, description="", prev_analysis=None, inter_procedure=False, options=None
    ):
        if not isinstance(name, str) or not name or not isinstance(id, str) or not id:
            raise ValueError("analysis name and id must be nonempty strings")
        if options is None:
            options = {}
        if not isinstance(options, Mapping):
            raise ValueError("analysis options must be a mapping")
        supported = {
            "dataflow analysis",
            "transform",
            "pointer analysis",
            "closure analysis",
            "ai analysis",
            "certified analysis",
        }
        if not isinstance(options.get("type"), str) or options["type"] not in supported:
            raise ValueError("options.type must name a supported analysis type")
        if not isinstance(description, str) or type(inter_procedure) is not bool:
            raise ValueError("description must be text and inter_procedure must be boolean")
        dependencies = [] if prev_analysis is None else prev_analysis
        if (
            not isinstance(dependencies, (list, tuple))
            or any(not isinstance(item, str) or not item for item in dependencies)
            or len(set(dependencies)) != len(dependencies)
        ):
            raise ValueError("prev_analysis must contain unique nonempty analysis names")
        self.name = name
        self.id = id
        self.description = description
        self.type = options["type"]
        self.inter_procedure = inter_procedure
        self.options = dict(options)
        self.prev_analysis = list(dependencies)

    def __str__(self):
        return f"""
AnalysisConfig(
    name={self.name},
    id={self.id},
    description={self.description},
    type={self.type},
    inter_procedure={self.inter_procedure},
    prev_analysis={self.prev_analysis},
    options={self.options}
)
"""

    def __repr__(self):
        return self.__str__()


def order_analysis_configs(configs):
    """Validate and order a complete dependency graph before running any pass."""
    by_name = {}
    for config in configs:
        if config.name in by_name:
            raise ValueError(f"Duplicate analysis name: {config.name}")
        by_name[config.name] = config
    successors = {name: [] for name in by_name}
    pending = {}
    for name, config in by_name.items():
        if len(set(config.prev_analysis)) != len(config.prev_analysis):
            raise ValueError(f"Duplicate dependencies for analysis: {name}")
        pending[name] = len(config.prev_analysis)
        for dependency in config.prev_analysis:
            if dependency not in by_name:
                raise ValueError(f"Unknown dependency {dependency!r} for analysis {name!r}")
            successors[dependency].append(name)
    ready = deque(name for name in by_name if pending[name] == 0)
    ordered = []
    while ready:
        name = ready.popleft()
        ordered.append(by_name[name])
        for successor in successors[name]:
            pending[successor] -= 1
            if pending[successor] == 0:
                ready.append(successor)
    if len(ordered) != len(by_name):
        blocked = sorted(name for name, count in pending.items() if count)
        raise ValueError(f"Cyclic analysis dependencies: {', '.join(blocked)}")
    return ordered


def get_scope_result(result, scope, dependency="analysis"):
    """Select predecessor facts without confusing another module's results.

    Maintained dataflow drivers expose ``per_scope`` alongside their historical
    flat ``in``/``out`` view. Transform drivers retain their scope-keyed mapping.
    A legacy flat dataflow result is accepted for direct single-scope callers.
    """
    if not isinstance(result, Mapping):
        raise ValueError(f"Dependency {dependency!r} does not expose per-scope results")
    if "per_scope" in result:
        scoped = result["per_scope"]
        if not isinstance(scoped, Mapping) or scope not in scoped:
            raise ValueError(f"Dependency {dependency!r} has no result for scope {scope!r}")
        return scoped[scope]
    if scope in result:
        return result[scope]
    if "in" in result and "out" in result:
        return {"in": result["in"], "out": result["out"]}
    raise ValueError(f"Dependency {dependency!r} has no result for scope {scope!r}")


class Analysis(ABC):
    analysis_dict: "Dict[str, Type[Analysis]]" = {}
    config: AnalysisConfig

    @abstractmethod
    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.valid_id()

    def __init_subclass__(cls):
        cls.analysis_dict[cls.__name__] = cls

    @classmethod
    def get_analysis(cls, id):
        return cls.analysis_dict[id]

    def get_id(self):
        return self.config.id

    def valid_id(self):
        cls_name = self.__class__.__name__
        assert cls_name == self.get_id(), "Invalid id for current Analysis"


class AnalysisDriver(ABC):
    config: AnalysisConfig
    results: Any

    @abstractmethod
    def __init__(self, config):
        self.config = config

    @abstractmethod
    def analyze(self, scope: IRScope, prev_results: Dict[str, Any]):
        pass
