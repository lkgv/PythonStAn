from abc import ABC, abstractmethod
from typing import Dict, Any


class AnalysisConfig:
    name: str
    id: str
    description: str
    options: Dict[str, Any]

    def __init__(self, name, id, description="", options={}):
        self.name = name
        self.id = id
        self.description = description
        self.options = options


class Analysis(ABC):
    analysis_dict: Dict[str, Any] = {}
    config: AnalysisConfig

    @abstractmethod
    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.valid_id()

    def __subclasshook__(cls, *args, **kwargs):
        super().__subclasshook__(*args, **kwargs)
        cls.analysis_dict[cls.__name__] = cls
    
    @classmethod
    def get_analysis(cls, id):
        return cls.analysis_dict[id]
    
    def get_id(self):
        return self.config.id        
    
    def valid_id(self):
        cls_name = self.__class__.__name__
        assert cls_name == self.get_id(), "Invalid id for current Analysis"
