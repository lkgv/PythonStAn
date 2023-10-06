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

    def propagate(self, s: State, bc: Tuple[BaseBlock, Context], overwrite: bool):
        blk, ctx = bc
        cur_state = None
        if not overwrite:
            states = self.get_state(ctx, blk)
            if len(states) > 0:
                cur_state = next(iter(states))
        s.set_block(blk)
        s.set_context(ctx)
        if cur_state is None:
            self.states[(ctx, blk)] = s
        else:
            cur_state.propagate(s)

    def num_of_states(self) -> int:
        return len(self.states)

    def get_call_graph(self) -> CallGraph:
        return self.call_graph


