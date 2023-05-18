from abc import ABC, abstractmethod
from typing import Dict, Any, Type, Literal, List
from pythonstan.graph.cfg import IRScope


class AnalysisConfig:
    name: str
    id: str
    description: str
    type: Literal['dataflow_analysis', 'transform']
    phase: str    # ['ast', 'three-address', 'ssa', ...]
    prev_analysis: List[str]  # previous analysis name
    options: Dict[str, Any]

    def __init__(self, name, id, phase, description="", prev_analysis=None, options=None):
        self.name = name
        self.id = id
        self.description = description
        self.type = options["type"]
        self.phase = phase
        if options is None:
            self.options = {}
        else:
            self.options = options
        if prev_analysis is None:
            self.prev_analysis = []
        else:
            self.prev_analysis = prev_analysis


class Analysis(ABC):
    analysis_dict: 'Dict[str, Type[Analysis]]' = {}
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

    @abstractmethod
    def __init__(self, config):
        self.config = config

    @abstractmethod
    def analyze(self, scope: IRScope):
        pass
