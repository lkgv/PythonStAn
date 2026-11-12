from typing import Set, Dict, List
from collections.abc import Mapping, Sequence
import os
import yaml

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.analysis import order_analysis_configs

__all__ = ["Config"]


def _mapping(value: object, message: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    return value


def _text(value: object, message: str) -> str:
    if not isinstance(value, str):
        raise ValueError(message)
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be boolean")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("import_level must be -1 or a nonnegative integer")
    return value


def _path(value: object, message: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, os.PathLike):
        try:
            decoded: object = value.__fspath__()
        except TypeError as exc:
            raise ValueError(message) from exc
        if isinstance(decoded, str):
            return decoded
    raise ValueError(message)


def _sequence(value: object, message: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(message)
    return value


def _dependencies(value: object) -> list[str] | tuple[str, ...] | None:
    if value is None:
        return None
    values = _sequence(value, "prev_analysis must contain unique nonempty analysis names")
    result = [
        _text(item, "prev_analysis must contain unique nonempty analysis names") for item in values
    ]
    return tuple(result) if isinstance(value, tuple) else result


class Config:
    filename: str
    project_path: str
    library_paths: List[str]
    analysis: Dict[str, AnalysisConfig]
    succ_analysis: Dict[str, Set[str]]
    lazy_ir_construction: bool
    import_level: int
    time_count: bool
    mock_libs: bool
    prefer_mock_libs: bool

    def __init__(
        self,
        filename,
        project_path=None,
        lazy_ir_construction: bool = False,
        import_level: int = -1,
        time_count: bool = False,
        mock_libs: bool = True,
        prefer_mock_libs: bool = False,
    ):
        try:
            self.filename = os.fspath(filename)
            self.project_path = (
                os.fspath(project_path)
                if project_path is not None
                else os.path.dirname(os.path.abspath(self.filename))
            )
        except TypeError as exc:
            raise ValueError("filename and project_path must be filesystem paths") from exc
        if (
            not isinstance(self.filename, str)
            or not self.filename
            or not isinstance(self.project_path, str)
            or not self.project_path
        ):
            raise ValueError("filename and project_path must be nonempty paths")
        if type(import_level) is not int or import_level < -1:
            raise ValueError("import_level must be -1 or a nonnegative integer")
        for name, value in (
            ("lazy_ir_construction", lazy_ir_construction),
            ("time_count", time_count),
            ("mock_libs", mock_libs),
            ("prefer_mock_libs", prefer_mock_libs),
        ):
            if type(value) is not bool:
                raise ValueError(f"{name} must be boolean")
        self.library_paths = []
        self.succ_analysis = {}
        self.analysis = {}
        self.lazy_ir_construction = lazy_ir_construction
        self.import_level = import_level
        self.time_count = time_count
        self.mock_libs = mock_libs
        self.prefer_mock_libs = prefer_mock_libs

    @classmethod
    def from_dict(cls, info: object) -> "Config":
        fields = _mapping(info, "pipeline configuration must be a mapping")
        allowed = {
            "filename",
            "project_path",
            "library_paths",
            "analysis",
            "lazy_ir_construction",
            "import_level",
            "time_count",
            "mock_libs",
            "prefer_mock_libs",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown pipeline options: {sorted(unknown, key=str)}")
        if "filename" not in fields:
            raise ValueError("filename is required")
        lazy_ir = _boolean(fields.get("lazy_ir_construction", False), "lazy_ir_construction")
        mock_libs = _boolean(fields.get("mock_libs", True), "mock_libs")
        prefer_mock_libs = _boolean(fields.get("prefer_mock_libs", False), "prefer_mock_libs")
        project = fields.get("project_path")
        conf = cls(
            _path(fields["filename"], "filename and project_path must be filesystem paths"),
            None
            if project is None
            else _path(project, "filename and project_path must be filesystem paths"),
            lazy_ir_construction=lazy_ir,
            import_level=_integer(fields.get("import_level", -1)),
            time_count=_boolean(fields.get("time_count", False), "time_count"),
            mock_libs=mock_libs,
            prefer_mock_libs=prefer_mock_libs,
        )
        analyses = _sequence(fields.get("analysis", []), "analysis and library_paths must be lists")
        paths = _sequence(
            fields.get("library_paths", []), "analysis and library_paths must be lists"
        )
        for raw_analysis in analyses:
            anal_info = _mapping(raw_analysis, "each analysis must be a mapping")
            unexpected = set(anal_info) - {
                "name",
                "id",
                "description",
                "prev_analysis",
                "inter_procedure",
                "options",
            }
            if unexpected:
                raise ValueError(f"Unknown analysis fields: {sorted(unexpected, key=str)}")
            if not {"name", "id"} <= set(anal_info):
                raise ValueError("each analysis requires name and id")
            inter_procedure = _boolean(anal_info.get("inter_procedure", False), "inter_procedure")
            raw_options = anal_info.get("options")
            anal_cfg = AnalysisConfig(
                _text(anal_info["name"], "analysis name and id must be nonempty strings"),
                _text(anal_info["id"], "analysis name and id must be nonempty strings"),
                _text(
                    anal_info.get("description", ""),
                    "description must be text and inter_procedure must be boolean",
                ),
                _dependencies(anal_info.get("prev_analysis")),
                inter_procedure,
                None
                if raw_options is None
                else _mapping(raw_options, "analysis options must be a mapping"),
            )
            conf.add_analysis(anal_cfg)
        for library_path in paths:
            conf.add_library_path(_path(library_path, "library paths must be filesystem paths"))
        return conf

    @classmethod
    def from_file(cls, filename: str | os.PathLike[str]) -> "Config":
        with open(filename, "r") as f:
            info: object = yaml.safe_load(f)
        return cls.from_dict(info)

    def add_analysis(self, cfg: AnalysisConfig):
        if cfg.name in self.analysis:
            raise ValueError(f"Duplicate analysis name: {cfg.name}")
        self.analysis[cfg.name] = cfg
        self.succ_analysis[cfg.name] = {*()}
        for prev_name in cfg.prev_analysis:
            if prev_name not in self.succ_analysis:
                self.succ_analysis[prev_name] = {*()}
            self.succ_analysis[prev_name].add(cfg.name)

    def add_library_path(self, path: str):
        try:
            path = os.fspath(path)
        except TypeError as exc:
            raise ValueError("library paths must be filesystem paths") from exc
        if not isinstance(path, str) or not path:
            raise ValueError("library paths must be nonempty strings")
        self.library_paths.append(path)

    def get_analysis_list(self) -> list[AnalysisConfig]:
        from .analysis_manager import DEFAULT_ANALYSIS

        ordered = order_analysis_configs([*DEFAULT_ANALYSIS, *self.analysis.values()])
        return [config for config in ordered if config.name in self.analysis]
