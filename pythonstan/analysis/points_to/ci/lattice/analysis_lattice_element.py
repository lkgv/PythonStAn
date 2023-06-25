from typing import Optional, Dict, Set, Tuple

from pythonstan.analysis.points_to.ci.lattice.state import State
from pythonstan.analysis.points_to.ci.lattice.context import Context
from pythonstan.graph.cfg import BaseBlock


class AnalysisLatticeElement:
    def get_state(self, ctx: Context, blk: Optional[BaseBlock] = None):
        ...

    def get_status(self, blk: BaseBlock) -> Dict[Context, State]:
        ...

    def get_status_with_entry_context(self, blk: BaseBlock, entry_ctx: Context) -> Set[State]:
        ...

    def propagate(self, s: State, bc: Tuple[BaseBlock, Context], localize: bool):
        ...

    def num_of_states(self) -> int:
        ...


