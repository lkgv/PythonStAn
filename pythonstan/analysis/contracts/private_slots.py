"""Opaque analyzer metadata slots; target strings can never collide with them."""
from enum import Enum

class _FunctionSlot(Enum):
    POSITIONAL_DEFAULT = "positional_default"
    KEYWORD_DEFAULT = "keyword_default"
    ANNOTATIONS = "annotations"

def positional_default_slot(index):
    if type(index) is not int or index < 0:raise ValueError("Expected a nonnegative default index")
    return (_FunctionSlot.POSITIONAL_DEFAULT,index)

def keyword_default_slot(name):
    if type(name) is not str:raise ValueError("Expected a keyword parameter name")
    return (_FunctionSlot.KEYWORD_DEFAULT,name)

def function_annotations_slot():return (_FunctionSlot.ANNOTATIONS,None)

def is_private_slot(key):
    return isinstance(key,tuple) and len(key)==2 and isinstance(key[0],_FunctionSlot)

def is_function_default_slot(key):
    return is_private_slot(key) and key[0] in {_FunctionSlot.POSITIONAL_DEFAULT,_FunctionSlot.KEYWORD_DEFAULT}
