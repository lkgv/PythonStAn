"""Retired private TAC semantic service.

Actual Python source semantics run through analysis.engines.abstract. This
module preserves import names solely to report explicit retirement errors;
no former algorithm remains active or copied into a second interpreter.
"""
from pythonstan.compat.shared_ai import BoundaryUnavailable

class _RetiredService:
    def __init__(self,*args,**kwargs):
        raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

class NameKind(_RetiredService):
    pass

class CellStore(_RetiredService):
    pass

class ScopeBinding(_RetiredService):
    pass

class ScopeSemantics(_RetiredService):
    pass

__all__=['NameKind', 'CellStore', 'ScopeBinding', 'ScopeSemantics']
