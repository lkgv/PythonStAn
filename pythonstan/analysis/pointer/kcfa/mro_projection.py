"""Live MRO projections from the same lexical base facts used by KCFA.

A list with unresolved order is a union of known ancestor candidates, not a
linearization. It may be used for union reads, never first-match exclusion.
"""
from .class_hierarchy import MROError, _c3_merge
from .heap_model import Field, FieldKind
from .object import ClassObject, BuiltinClassObject, BuiltinFunctionObject


class MROProjection(list):
    def __init__(self, values, *, order_resolved, reasons=()):
        super().__init__(values)
        self.order_resolved = order_resolved
        self.residual_reasons = tuple(reasons)
        self.is_live_projection = True


def project_class_mro(state, root, hierarchy=None, on_read=None, *, read_only=False):
    """Project ordered source slots, declining order for merged alternatives.

    No cross-query cache is used: later base objects must immediately expand a
    previous query. Exact ordering remains conditional on ordinary class/MRO
    semantics; this function never certifies class construction effects.
    """
    known, seen, active, cache, reasons = [], set(), set(), {}, []

    def remember(value):
        if value not in seen:
            seen.add(value)
            known.append(value)

    def unresolved(reason):
        if reason not in reasons:
            reasons.append(reason)

    def visit(cls):
        remember(cls)
        if cls in active:
            unresolved("cyclic base relation")
            return [cls], False
        if cls in cache:
            return cache[cls]
        if not isinstance(cls, ClassObject):
            return [cls], isinstance(cls, BuiltinClassObject)
        active.add(cls)
        ordered = True
        slots = []
        variables = getattr(cls.ir, "_kcfa_base_variables", None)
        if variables is not None:
            owner = cls.container_scope
            if getattr(cls.ir, "_kcfa_dynamic_bases", False) or any(
                    var.name.startswith("$dynamic_bases_") for var in variables):
                unresolved("dynamic bases were aggregated without tuple order")
                ordered = False
            for variable in variables:
                binding = (state.find_variable(owner, owner.context, variable) if read_only
                           else state.get_variable(owner, owner.context, variable))
                if on_read is not None:
                    on_read(binding)
                points = list(state.get_points_to(binding))
                candidates = [obj for obj in points if isinstance(obj, (ClassObject, BuiltinClassObject))]
                if len(points) != 1 or len(candidates) != 1:
                    unresolved("base slot has missing, unknown or multiple candidates")
                    ordered = False
                slots.append(candidates)
            # User metaclasses may override mro(). Known lexical bases alone do
            # not authorize first-match reads through such a class.
            meta = Field(FieldKind.CLASS_METADATA, "metaclass")
            explicit_meta = any(keyword.arg == "metaclass" for keyword in cls.ir.keywords)
            if explicit_meta or (cls, meta) in state._field_accesses:
                if read_only:
                    access = state.has_field(owner, owner.context, cls, meta)
                    from .context import Ctx
                    location = Ctx(cls.context, None, access) if access is not None else None
                else:
                    location = state.get_field(owner, owner.context, cls, meta)
                if on_read is not None:
                    on_read(location)
                metaclasses = list(state.get_points_to(location))
                if explicit_meta and not metaclasses:
                    unresolved("explicit metaclass binding has no resolved value")
                    ordered = False
                for value in metaclasses:
                    builtin_type = (isinstance(value, BuiltinClassObject) and value.builtin_name == "type") or (
                        isinstance(value, BuiltinFunctionObject) and value.function_name == "type")
                    if not builtin_type:
                        unresolved("custom or unknown metaclass MRO")
                        ordered = False
        elif hierarchy is not None and hierarchy.has_class(cls):
            slots = [[base] for base in hierarchy.get_bases(cls)]
        elif cls.ir.get_bases():
            unresolved("class has no source-bound base variable metadata")
            ordered = False

        bases, parents = [], []
        for candidates in slots:
            for base in candidates:
                parent, parent_ordered = visit(base)
                ordered = ordered and parent_ordered
                parents.append(parent)
                bases.append(base)
        if ordered:
            try:
                if len(set(bases)) != len(bases):
                    raise MROError("duplicate base")
                result = [cls, *_c3_merge(parents + [bases])]
            except MROError:
                unresolved("inconsistent C3 order")
                ordered = False
        if not ordered:
            result = [cls]
            for parent in parents:
                for value in parent:
                    if value not in result:
                        result.append(value)
        active.remove(cls)
        cache[cls] = result, ordered
        return result, ordered

    result, ordered = visit(root)
    if not ordered:
        # Known candidates cannot represent arbitrary unknown classes. Record
        # that missing semantic obligation regardless of diagnostic settings.
        if not read_only:
            state._residual_effects[(root, "mro_projection")] = {
                "origin": str(root), "context": str(getattr(root, "context", "")),
                "reason": "; ".join(reasons), "return_target": None,
                "effects": ["unresolved_mro_order", "unknown_inherited_attribute", "may_raise"],
            }
        result = known
    return MROProjection(result, order_resolved=ordered, reasons=reasons)
