from .state import State


class CallEdge:
    s: State

    def get_state(self) -> State:
        return self.s

    def set_state(self, s: State):
        self.s = s

    def clone(self) -> 'CallEdge':
        return CallEdge(self)
