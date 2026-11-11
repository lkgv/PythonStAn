from typing import Set, Dict, List
from collections.abc import Mapping
import os
import yaml

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.analysis import order_analysis_configs

__all__ = ["Config"]


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

    def __init__(self, filename, project_path=None,
                 lazy_ir_construction: bool = False,
                 import_level: int = -1,
                 time_count: bool = False,
                 mock_libs: bool = True,
                 prefer_mock_libs: bool = False):
        try:
            self.filename = os.fspath(filename)
            self.project_path = os.fspath(project_path) if project_path is not None else os.path.dirname(os.path.abspath(self.filename))
        except TypeError as exc:
            raise ValueError("filename and project_path must be filesystem paths") from exc
        if not isinstance(self.filename, str) or not self.filename or not isinstance(self.project_path, str) or not self.project_path:
            raise ValueError("filename and project_path must be nonempty paths")
        if type(import_level) is not int or import_level < -1:
            raise ValueError("import_level must be -1 or a nonnegative integer")
        for name, value in (("lazy_ir_construction", lazy_ir_construction),
                            ("time_count", time_count), ("mock_libs", mock_libs),
                            ("prefer_mock_libs", prefer_mock_libs)):
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
    def from_dict(cls, info: Dict):
        if not isinstance(info, Mapping):
            raise ValueError("pipeline configuration must be a mapping")
        allowed = {"filename", "project_path", "library_paths", "analysis",
                   "lazy_ir_construction", "import_level", "time_count",
                   "mock_libs", "prefer_mock_libs"}
        unknown = set(info) - allowed
        if unknown:
            raise ValueError(f"Unknown pipeline options: {sorted(unknown, key=str)}")
        if "filename" not in info:
            raise ValueError("filename is required")
        lazy_ir = info.get('lazy_ir_construction', False)
        mock_libs = info.get('mock_libs', True)
        prefer_mock_libs = info.get('prefer_mock_libs', False)
        conf = cls(
            info['filename'],
            info.get('project_path'),
            lazy_ir_construction=lazy_ir,
            import_level=info.get('import_level', -1),
            time_count=info.get('time_count', False),
            mock_libs=mock_libs,
            prefer_mock_libs=prefer_mock_libs,
        )
        analyses = info.get('analysis', [])
        paths = info.get('library_paths', [])
        if not isinstance(analyses, (list, tuple)) or not isinstance(paths, (list, tuple)):
            raise ValueError("analysis and library_paths must be lists")
        for anal_info in analyses:
            if not isinstance(anal_info, Mapping):
                raise ValueError("each analysis must be a mapping")
            unexpected = set(anal_info) - {"name", "id", "description", "prev_analysis", "inter_procedure", "options"}
            if unexpected:
                raise ValueError(f"Unknown analysis fields: {sorted(unexpected, key=str)}")
            if not {"name", "id"} <= set(anal_info):
                raise ValueError("each analysis requires name and id")
            inter_procedure = anal_info.get('inter_procedure', False)
            anal_cfg = AnalysisConfig(
                anal_info['name'], anal_info['id'], anal_info.get('description', ''),
                anal_info.get('prev_analysis'), inter_procedure, anal_info.get('options'))
            conf.add_analysis(anal_cfg)
        for library_path in paths:
            conf.add_library_path(library_path)
        return conf

    @classmethod
    def from_file(cls, filename):
        with open(filename, 'r') as f:
            info = yaml.safe_load(f)
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

    def get_analysis_list(self):
        from .analysis_manager import DEFAULT_ANALYSIS
        ordered = order_analysis_configs([*DEFAULT_ANALYSIS, *self.analysis.values()])
        return [config for config in ordered if config.name in self.analysis]
