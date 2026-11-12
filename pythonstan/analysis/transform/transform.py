from abc import abstractmethod

from pythonstan.ir import IRScope

from ..analysis import Analysis, AnalysisConfig


class Transform(Analysis):
    module: IRScope
    inputs: dict[str, object]
    results: object

    @abstractmethod
    def __init__(self, config: AnalysisConfig) -> None:
        super().__init__(config)
        self.inputs = {}

    def set_input(self, key: str, value: object) -> None:
        self.inputs[key] = value

    def get_input(self, key: str) -> object:
        return self.inputs[key]

    @abstractmethod
    def transform(self, module: IRScope) -> None: ...
