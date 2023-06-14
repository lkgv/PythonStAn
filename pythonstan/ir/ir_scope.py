from typing import *
import ast
from abc import ABC, abstractmethod


__all__ = ["IRScope"]


class IRScope(ABC):
    qualname: str

    @abstractmethod
    def __init__(self, qualname: str):
        self.qualname = qualname

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    def get_qualname(self) -> str:
        return self.qualname

