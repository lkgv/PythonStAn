"""Retired private TAC semantic service.

Actual Python source semantics run through analysis.engines.abstract. This
module preserves import names solely to report explicit retirement errors;
no former algorithm remains active or copied into a second interpreter.
"""
from pythonstan.compat.shared_ai import BoundaryUnavailable

class _RetiredService:
    def __init__(self,*args,**kwargs):
        raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

def is_generator_function(*args,**kwargs):
    raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

def _get_ir_statements(*args,**kwargs):
    raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

def _get_call_context(*args,**kwargs):
    raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

class _DispatchResult(_RetiredService):
    pass

class IteratorBuiltinsSemantics(_RetiredService):
    pass

class GeneratorSemantics(_RetiredService):
    pass

__all__=['is_generator_function', '_get_ir_statements', '_get_call_context', '_DispatchResult', 'IteratorBuiltinsSemantics', 'GeneratorSemantics']
