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
        self.succ_analysis = {}

    @classmethod
    def from_file(cls, filename):
        with open(filename, 'r') as f:
            info = yaml.safe_load(f)
        cfg = cls(info['filename'], info['project_path'])
        for anal_info in info['analysis']:
            anal_cfg = AnalysisConfig(
                anal_info['name'], anal_info['id'], anal_info['description'],
                anal_info['prev_analysis'], anal_info['options'])
            cfg.add_analysis(anal_cfg)
        for library_path in info['library_paths']:
            cfg.add_library_path(library_path)
        return cfg

    def add_analysis(self, cfg: AnalysisConfig):
        self.analysis[cfg.id] = cfg
        for prev_id in cfg.prev_analysis:
            if prev_id in self.succ_analysis:
                self.succ_analysis[prev_id].add(cfg.id)
            else:
                self.succ_analysis[prev_id] = {cfg.id}

    def add_library_path(self, path: str):
        self.library_paths.append(path)

    def get_analysis_list(self):
        analysis_id_list = topo_sort(self.succ_analysis)
        return [self.analysis[anal_id] for anal_id in analysis_id_list]
