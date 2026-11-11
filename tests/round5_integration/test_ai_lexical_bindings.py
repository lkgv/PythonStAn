"""Compare lexical binding extraction with CPython's static symbol tables.

No source program is executed; imports in these cases are syntax only.
"""
import ast
import symtable
import pytest
from pythonstan.analysis.pointer.ai.lexical_bindings import local_bindings


@pytest.mark.parametrize("body", [
    "return len([1])",
    "len = callback\nreturn len([1])",
    "del len\nreturn len([1])",
    "try:\n pass\nexcept Exception as len:\n pass\nreturn len([1])",
    "import imaginary.module as len\nreturn len([1])",
    "from imaginary import thing as len\nreturn len([1])",
    "values = [len for len in items]\nreturn len([1])",
    "values = [(len := x) for x in items]\nreturn len([1])",
    "callback = lambda len: len\nreturn len([1])",
    "callback = lambda x=(len := replacement): x\nreturn len([1])",
    "def len(x):\n return x\nreturn len([1])",
    "class len:\n pass\nreturn len([1])",
    "global len\nlen = callback\nreturn len([1])",
    "match data:\n case {'key': len, **rest}:\n  pass\nreturn len([1])",
])
def test_local_names_match_static_python_symbol_table(body):
    source = "def subject(items, callback, replacement, data):\n" + "\n".join("    " + line for line in body.splitlines())
    function = ast.parse(source).body[0]
    symbols = symtable.symtable(source, "subject.py", "exec").get_children()[0]
    # Python 3.12's inlined comprehensions may publish hidden iteration slots
    # as locals in symtable. They do not leak a source-level binding. Probe len,
    # which is explicitly read after every comprehension in these examples.
    bindings = local_bindings(function)
    assert ("len" in bindings) == symbols.lookup("len").is_local()
    assert {"items", "callback", "replacement", "data"} <= bindings
