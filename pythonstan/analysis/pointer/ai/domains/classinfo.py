"""Retired private TAC semantic service.

Actual Python source semantics run through analysis.engines.abstract. This
module preserves import names solely to report explicit retirement errors;
no former algorithm remains active or copied into a second interpreter.
"""
from pythonstan.compat.shared_ai import BoundaryUnavailable

class _RetiredService:
    def __init__(self,*args,**kwargs):
        raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

class ClassInfo(_RetiredService):
    pass

class ClassInfoCache(_RetiredService):
    pass

def get_class_info(*args,**kwargs):
    raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

def create_dynamic_class_info(*args,**kwargs):
    raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

def compute_mro_from_bases(*args,**kwargs):
    raise BoundaryUnavailable("Retired private TAC service; use source-backed AISummaryEngine/Driver")

__all__=['ClassInfo', 'ClassInfoCache', 'get_class_info', 'create_dynamic_class_info', 'compute_mro_from_bases']
