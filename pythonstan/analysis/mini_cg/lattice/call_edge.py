from .state import State


class CallEdge:
    s: State

    def __init__(self, s: State):
        self.s = s

    def get_state(self) -> State:
        return self.s

    def set_state(self, s: State):
        self.s = s

    def clone(self) -> 'CallEdge':
        return CallEdge(self)
