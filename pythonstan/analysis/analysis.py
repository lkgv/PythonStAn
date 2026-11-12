from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable, Mapping
from typing import ClassVar, Literal, override

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
    prev_analysis: list[str]  # previous analysis name
    options: dict[object, object]

    def __init__(
        self,
        name: str,
        id: str,
        description: str = "",
        prev_analysis: list[str] | tuple[str, ...] | None = None,
        inter_procedure: bool = False,
        options: Mapping[str, object] | Mapping[object, object] | None = None,
    ) -> None:
        if not isinstance(name, str) or not name or not isinstance(id, str) or not id:
            raise ValueError("analysis name and id must be nonempty strings")
        if options is None:
            options = {}
        if not isinstance(options, Mapping):
            raise ValueError("analysis options must be a mapping")
        raw_kind = options.get("type")
        if not isinstance(raw_kind, str):
            raise ValueError("options.type must name a supported analysis type")
        match raw_kind:
            case (
                "dataflow analysis"
                | "transform"
                | "pointer analysis"
                | "closure analysis"
                | "ai analysis"
                | "certified analysis"
            ) as kind:
                pass
            case _:
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
        self.type = kind
        self.inter_procedure = inter_procedure
        copied_options: dict[object, object] = {}
        for key, value in dict(options).items():
            copied_options[key] = value
        self.options = copied_options
        self.prev_analysis = list(dependencies)

    @override
    def __str__(self) -> str:
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

    @override
    def __repr__(self) -> str:
        return self.__str__()


def order_analysis_configs(configs: Iterable[AnalysisConfig]) -> list[AnalysisConfig]:
    """Validate and order a complete dependency graph before running any pass."""
    by_name = {}
    for config in configs:
        if config.name in by_name:
            raise ValueError(f"Duplicate analysis name: {config.name}")
        by_name[config.name] = config
    successors: dict[str, list[str]] = {name: [] for name in by_name}
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


def get_scope_result(result: object, scope: IRScope, dependency: str = "analysis") -> object:
    """Select predecessor facts without confusing another module's results.

    Maintained dataflow drivers expose ``per_scope`` alongside their historical
    flat ``in``/``out`` view. Transform drivers retain their scope-keyed mapping.
    A legacy flat dataflow result is accepted for direct single-scope callers.
    """
    if not isinstance(result, Mapping):
        raise ValueError(f"Dependency {dependency!r} does not expose per-scope results")
    result_map: Mapping[object, object] = result
    if "per_scope" in result_map:
        scoped = result_map["per_scope"]
        if not isinstance(scoped, Mapping):
            raise ValueError(f"Dependency {dependency!r} has no result for scope {scope!r}")
        scope_map: Mapping[object, object] = scoped
        if scope not in scope_map:
            raise ValueError(f"Dependency {dependency!r} has no result for scope {scope!r}")
        return scope_map[scope]
    if scope in result_map:
        return result_map[scope]
    if "in" in result_map and "out" in result_map:
        return {"in": result_map["in"], "out": result_map["out"]}
    raise ValueError(f"Dependency {dependency!r} has no result for scope {scope!r}")


class Analysis(ABC):
    analysis_dict: ClassVar[dict[str, type["Analysis"]]] = {}
    config: AnalysisConfig

    @abstractmethod
    def __init__(self, config: AnalysisConfig) -> None:
        self.config = config
        self.valid_id()

    @override
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.analysis_dict[cls.__name__] = cls

    @classmethod
    def get_analysis(cls, id: str) -> type["Analysis"]:
        return cls.analysis_dict[id]

    def get_id(self) -> str:
        return self.config.id

    def valid_id(self) -> None:
        cls_name = self.__class__.__name__
        assert cls_name == self.get_id(), "Invalid id for current Analysis"


class AnalysisDriver(ABC):
    config: AnalysisConfig
    results: object

    @abstractmethod
    def __init__(self, config: AnalysisConfig) -> None:
        self.config = config

    @abstractmethod
    def analyze(self, scope: IRScope, prev_results: dict[str, object]) -> object:
        pass
