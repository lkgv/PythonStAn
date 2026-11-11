"""Pure builtin exception construction for a finite declared model family.

This module constructs abstract values and args tuples; it never imports or
executes analyzed source. Exception groups and specialized OSError construction
remain outside this model and retain their ordinary open fallback.
"""
import ast
from dataclasses import replace
from pythonstan.models.builtins import EXCEPTION_TYPES
from .allocation import canonical_constant
from .constraints import StoreConstraint, ResidualEffectConstraint
from .heap_model import attr, key, elem
from .object import AllocKind, AllocSite, BuiltinClassObject, ObjectFactory, TupleObject, UnknownValue
from .points_to_set import PointsToSet
from .variable import Variable, VariableKind


def seed_referenced_exceptions(driver, module_scope, context):
    tree = module_scope.stmt.get_ast()
    names = sorted({node.id for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id in EXCEPTION_TYPES}) if isinstance(tree, ast.AST) else []
    if not names:
        return
    state = driver.state
    cache = getattr(state, "_builtin_exception_classes", None)
    if cache is None:
        cache = state._builtin_exception_classes = {}
    for name in names:
        obj = cache.get(name)
        if obj is None:
            model = ObjectFactory.create_builtin_class(name, context)
            selected = state.select_allocation_context(context, model.alloc_site, identity="canonical")
            obj = BuiltinClassObject(selected, model.alloc_site, name)
            cache[name] = obj
            state._heap.set_obj(module_scope, selected, model.alloc_site, obj)
            state.obj_scope[obj] = module_scope
        variable = state.get_variable(module_scope, context, Variable(name, VariableKind.GLOBAL))
        driver.solver.handle_new_points_to(variable, module_scope, PointsToSet.singleton(obj))


def construct_exception(handler, scope, context, call, name):
    if name not in EXCEPTION_TYPES:
        raise ValueError("Exception constructor not in the declared model family")
    state = handler.state
    target = call.target or handler._make_temp_var("discarded_exception", call)
    effective_call = call if call.target is not None else replace(call, target=target)
    handler._allocate_builtin_instance_to_target(scope, context, effective_call,
        builtin_type=name, alloc_kind=AllocKind.OBJECT, alloc_suffix="exception:" + name)
    args_variable = handler._make_temp_var("exception_args", call)
    site = AllocSite(("exception_args", call.call_site, name), AllocKind.TUPLE)
    args_object = state._heap.get_obj(scope, context, site)
    if args_object is None:
        args_object = TupleObject(state.select_allocation_context(context, site), site)
        state._heap.set_obj(scope, context, site, args_object)
        state.obj_scope[args_object] = scope
    handler._add_points_to_target(scope, context, args_variable, args_object)
    constraints = [StoreConstraint(target, attr("args"), args_variable)]
    for index, argument in enumerate(call.args):
        constraints.extend((StoreConstraint(args_variable, key(index), argument),
                            StoreConstraint(args_variable, elem(), argument)))
    if name == "StopIteration":
        if call.args:
            value_variable = call.args[0]
        else:
            value_variable = handler._make_temp_var("stop_iteration_value", call)
            none_site = AllocSite(("exception_default_value", call.call_site), AllocKind.CONSTANT)
            none = canonical_constant(state, context, none_site, None)
            handler._add_points_to_target(scope, context, value_variable, none)
        constraints.append(StoreConstraint(target, attr("value"), value_variable))
    allowed=({"name","obj"} if name=="AttributeError"
             else {"name"} if name in {"NameError","UnboundLocalError"}
             else {"name","path"} if name=="ImportError" else set())
    provided={keyword for keyword,_ in call.kwargs if keyword in allowed}
    for keyword in allowed-provided:
        value_variable=handler._make_temp_var("exception_default_"+keyword,call)
        none_site=AllocSite(("exception_keyword_default",call.call_site,keyword),AllocKind.CONSTANT)
        handler._add_points_to_target(scope,context,value_variable,
                                     canonical_constant(state,context,none_site,None))
        constraints.append(StoreConstraint(target,attr(keyword),value_variable))
    unknown_keywords=any(keyword in {None,"**"} for keyword,_ in call.kwargs)
    for keyword,variable in call.kwargs:
        if keyword in allowed:
            constraints.append(StoreConstraint(target,attr(keyword),variable))
    has_star=any(starred for _,starred in getattr(call.stmt,"get_args",lambda:())())
    if unknown_keywords or has_star:
        unknown_variable=handler._make_temp_var("exception_unknown_argument",call)
        unknown_site=AllocSite(call.stmt,AllocKind.UNKNOWN)
        opaque=UnknownValue(state.select_allocation_context(context,unknown_site),unknown_site)
        handler._add_points_to_target(scope,context,unknown_variable,opaque)
        if unknown_keywords:
            constraints.extend(StoreConstraint(target,attr(keyword),unknown_variable) for keyword in allowed)
        if has_star:
            constraints.append(StoreConstraint(target,attr("args"),unknown_variable))
            if name=="StopIteration":constraints.append(StoreConstraint(target,attr("value"),unknown_variable))
    invalid=unknown_keywords or has_star or any(keyword not in allowed for keyword,_ in call.kwargs)
    if invalid:
        constraints.append(ResidualEffectConstraint(str(call.call_site),
            "Builtin exception argument expansion/validation may raise TypeError; supported keyword attrs remain covered; no arbitrary constructor heap mutation is implied",
            ("exception_argument_validation","may_raise")))
    return constraints
