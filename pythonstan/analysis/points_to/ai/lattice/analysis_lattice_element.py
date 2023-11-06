from typing import Optional, Dict, Set, Tuple

from .state import State
from .context import Context
from .call_graph import CallGraph
from pythonstan.graph.cfg import BaseBlock



class AnalysisLatticeElement:
    def get_state(self, ctx: Context, blk: Optional[BaseBlock] = None):
        if blk is not None:
            return self.


    def get_states(self, blk: BaseBlock) -> Dict[Context, State]:
        ...

    def get_states_with_entry_context(self, blk: BaseBlock, entry_ctx: Context) -> Set[State]:
        ...

    def propagate(self, s: State, bc: Tuple[BaseBlock, Context], localize: bool):
        ...

    def num_of_states(self) -> int:
        ...

    def get_call_graph(self) -> CallGraph:
        ...


