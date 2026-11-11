"""Real IR-backed test data; constructors mirror the current public domain model.

Every call creates a fresh IR identity. Source coordinates and display names are
not interning keys: callers testing equal sites must reuse the same statement.
"""
import ast
from pythonstan.ir.ir_statements import IRAssign, IRClass, IRFunc, IRModule
from pythonstan.analysis.pointer.kcfa import AllocKind, AllocSite, CallStringContext, Scope, Variable, VariableKind
from pythonstan.analysis.pointer.kcfa.context import Ctx
from pythonstan.analysis.pointer.kcfa.object import ModuleObject, FunctionObject, ClassObject


def make_site(file="test.py", line=1, col=0, kind=AllocKind.OBJECT, name=None):
    if kind in (AllocKind.FUNCTION, AllocKind.METHOD):
        node = ast.parse(f"def {name or 'function'}(): pass").body[0]
        stmt = IRFunc(name or "function", node, is_method=kind == AllocKind.METHOD)
    elif kind == AllocKind.CLASS:
        node = ast.parse(f"class {name or 'Class'}: pass").body[0]
        stmt = IRClass(name or "Class", node)
    elif kind == AllocKind.MODULE:
        node = ast.parse("")
        stmt = IRModule(name or "module", node, filename=file)
    else:
        node = ast.parse("value = None").body[0]
        stmt = IRAssign(node)
    node.lineno = line
    node.col_offset = col
    return AllocSite(stmt, kind)


def make_scope(name="test", kind="function", context=None):
    context = context or CallStringContext((), 2)
    module_ir = IRModule(name if kind == "module" else name.split('.')[0], ast.parse(""), filename="test.py")
    module_obj = ModuleObject(context, AllocSite(module_ir, AllocKind.MODULE), module_ir)
    module = Scope.new(module_obj, None, context, module_ir)
    if kind == "module":
        return module
    alloc_kind = AllocKind.CLASS if kind == "class" else AllocKind.METHOD if kind == "method" else AllocKind.FUNCTION
    site = make_site(kind=alloc_kind, name=name.rsplit('.', 1)[-1])
    site.stmt.qualname = name
    if kind == "class":
        obj = ClassObject(context, site, module, site.stmt)
    else:
        obj = FunctionObject(context, site, module, site.stmt)
    return Scope.new(obj, module, context, site.stmt, parent=module)


def qualify(name, scope, context, kind=VariableKind.LOCAL):
    return Ctx(context, scope, Variable(name, kind))
