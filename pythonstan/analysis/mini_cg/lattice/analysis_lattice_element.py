from typing import Optional, Dict, Set, Tuple

from .state import State
from .context import Context
from .call_graph import CallGraph
from pythonstan.graph.cfg import BaseBlock


class AnalysisLatticeElement:
    states: Dict[Tuple[Context, BaseBlock], State]
    call_graph: CallGraph

    def __init__(self, states, call_graph):
        self.states = states
        self.call_graph = call_graph

    def get_state(self, ctx: Context, blk: Optional[BaseBlock] = None):
        if blk is None:
            return {s for (c, b), s in self.states.items() if ctx == c}
        else:
            s = self.states.get((ctx, blk))
            return {s} if s is not None else set()

    def get_states(self, blk: BaseBlock) -> Dict[Context, State]:
        return {c: s for (c, b), s in self.states.items() if blk == b}

    def propagate(self, s: State, bc: Tuple[BaseBlock, Context], localize: bool):
        ...

    def num_of_states(self) -> int:
        return len(self.states)

    def get_call_graph(self) -> CallGraph:
        return self.call_graph


