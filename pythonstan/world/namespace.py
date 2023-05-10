from typing import List

from pythonstan.ir import IRImport


class Namespace:
    names: List[str]

    def __init__(self, names: List[str]):
        self.names = names

    def __str__(self):
        self.to_str()

    def from_str(self, filename: str):
        self.names = filename.split('/')

    def from_import(self, stmt: IRImport):
        ...

    def to_str(self):
        return '.'.join(self.names)
