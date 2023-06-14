from typing import Set, Dict, List
from queue import Queue
import yaml

from pythonstan.analysis import AnalysisConfig
from pythonstan.utils.common import topo_sort

__all__ = ["Config"]


class Config:
    filename: str
    project_path: str
    library_paths: List[str]
    analysis: Dict[str, AnalysisConfig]
    succ_analysis: Dict[str, Set[str]]

    def __init__(self, filename, project_path):
        self.filename = filename
        self.project_path = project_path
        self.library_paths = []
        self.succ_analysis = {}
        self.analysis = {}

    @classmethod
    def from_dict(cls, info: Dict):
        conf = cls(info['filename'], info['project_path'])
        for anal_info in info['analysis']:
            inter_procedure = anal_info.get('inter_procedure', False)
            anal_cfg = AnalysisConfig(
                anal_info['name'], anal_info['id'], anal_info['description'],
                anal_info['prev_analysis'], inter_procedure, anal_info['options'])
            conf.add_analysis(anal_cfg)
        for library_path in info['library_paths']:
            conf.add_library_path(library_path)
        return conf

    @classmethod
    def from_file(cls, filename):
        with open(filename, 'r') as f:
            info = yaml.safe_load(f)
        return cls.from_dict(info)

    def add_analysis(self, cfg: AnalysisConfig):
        self.analysis[cfg.name] = cfg
        self.succ_analysis[cfg.name] = []
        for prev_name in cfg.prev_analysis:
            if prev_name not in self.succ_analysis:
                self.succ_analysis[prev_name] = {*()}
            self.succ_analysis[prev_name].add(cfg.name)

    def add_library_path(self, path: str):
        self.library_paths.append(path)

    def get_analysis_list(self):
        analysis_id_list = topo_sort(self.succ_analysis)
        print(self.analysis)
        return [self.analysis[anal_id] for anal_id in analysis_id_list if anal_id in self.analysis]
