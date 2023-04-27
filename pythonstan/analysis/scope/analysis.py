from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional

from pythonstan.graph.cfg.models import CFGFunc, CFGClass, CFGModule
from ..analysis import Analysis, AnalysisConfig

Fact = TypeVar('Fact')


class ScopeAnalysis(Generic[Fact], Analysis):
    @abstractmethod
    def __init__(self, config: AnalysisConfig):
        super(Analysis, self.__class__).__init__(config)
    
    @abstractmethod
    def analyze_function(self, fn: CFGFunc, fact: Optional[Fact]=None) -> Fact:
        pass

    @abstractmethod
    def analyze_class(self, cls: CFGClass, fact: Optional[Fact]=None) -> Fact:
        pass

    @abstractmethod
    def analyze_module(self, mod: CFGModule, fact: Optional[Fact]=None) -> Fact:
        pass

    @abstractmethod
    def init_function(self, fn: CFGFunc) -> Fact:
        pass

    @abstractmethod
    def init_class(self, cls: CFGClass) -> Fact:
        pass

    @abstractmethod
    def init_module(self, mod: CFGModule) -> Fact:
        pass
