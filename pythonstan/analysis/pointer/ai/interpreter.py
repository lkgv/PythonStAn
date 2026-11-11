"""Retired source-less TAC interface; source-backed scopes use shared execution."""
from dataclasses import dataclass,field
from pythonstan.compat.shared_ai import BoundaryUnavailable

@dataclass
class TransferResult:
    states:list=field(default_factory=list)
    is_terminal:bool=False
    exceptional_states:list=field(default_factory=list)
    @classmethod
    def single(cls,state,next_label=None):return cls([(next_label,state)])
    @classmethod
    def terminal(cls):return cls([],True)

class IRInterpreter:
    def __init__(self,pta_query=None,budget=None,scope_ir=None,caller_scope=None,call_context=None,recursion_depth=0,solver_config=None,work_ledger=None,**kwargs):
        self.pta_query,self.budget,self.scope_ir=pta_query,budget,scope_ir
        self.caller_scope,self.call_context=caller_scope,call_context
        self.solver_config,self.work_ledger=solver_config,work_ledger
    def execute_scope(self,initial_state):
        from pythonstan.compat.shared_ai import execute_scope
        if self.scope_ir is None:raise BoundaryUnavailable('Original source scope required')
        return execute_scope(self.scope_ir,initial_state,self.pta_query,self.budget,self.caller_scope,self.call_context,self.solver_config,self.work_ledger)[0]
    def transfer(self,*args,**kwargs):
        raise BoundaryUnavailable('Private source-less TAC transfer is retired; use source-backed AISolver')
    def __getattr__(self,name):
        if name.startswith('_') or name in {'call_semantics','scope_semantics','condition_refiner'}:
            raise BoundaryUnavailable('Retired private TAC service: '+name)
        raise AttributeError(name)
