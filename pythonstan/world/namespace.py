from typing import List

from pythonstan.ir import IRImport


class Namespace:
    names: List[str]

    def __init__(self, names: List[str]):
        self.names = names

    def __str__(self):
        self.to_str()

    @classmethod
    def from_str(cls, filename: str) -> 'Namespace':
        names = filename.split('/')
        return cls(names)

    def from_import(self, stmt: IRImport):
        ...

    def to_str(self):
        return '.'.join(self.names)

    def module_name(self):
        return self.names[-1]
