"""Persistent receiver-dependent may candidates for ordinary ``super`` lookup.

The closure intentionally does not enforce first-match C3 precedence. Every
represented ordinary C3 suffix class is an ancestor of the actual receiver's
lookup root. Joining all such ancestors (including possible earlier siblings)
therefore restores may coverage without a must-presence/order assumption.
Unknown native bases, custom MROs, descriptors and class mutation remain open.
"""
from .heap_model import Field, FieldKind
from .object import ClassObject, FunctionObject, InstanceObject, MethodObject
from .points_to_set import PointsToSet
from .pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from .variable import VariableKind


def install_super_lookup(state, scope, context, proxy, field, target):
    """Install a monotone, late-growing class/base/attribute dependency graph.

    Initially skip root/current direct slots while still traversing their bases.
    Multiple represented paths, cycles or aggregated dynamic bases revoke that
    exclusion: distinct concrete classes may share one abstract allocation.
    A class visited as one root can still contribute as another root's ancestor.
    Invalid receivers and custom MRO behavior remain residuals.
    """
    current = proxy.current_class
    if not isinstance(current, ClassObject):
        return
    receiver = proxy.instance_obj
    watched = set()
    attached = set()
    bases = {}
    incoming_slots = {}
    ambiguous = set()
    observed_base_edges = set()

    residuals = getattr(state, "_residual_effects", None)
    if residuals is None:
        residuals = state._residual_effects = {}
    residual = residuals[(proxy, field, "super_receiver_ancestor_closure")] = {
        "origin": str(proxy), "context": str(context), "return_target": str(field),
        "reason": "Receiver ancestor may-closure; exact C3 order, receiver validity, custom MRO/native bases, class mutation and descriptors remain unproved",
        "effects": ["super_receiver_check", "super_mro_suffix", "descriptor_precedence",
                    "class_hierarchy_mutation", "unknown_inherited_attribute", "may_raise"],
    }

    def bind(_edge, points):
        if isinstance(receiver, InstanceObject):
            return points.inherit_to(receiver.class_obj).deliver_into(receiver)
        if isinstance(receiver, ClassObject):
            # Class receivers support ordinary subclass lookup and metaclass
            # instance lookup; retain both until that relation is established.
            inherited = points.inherit_to(receiver)
            bound = []
            for value in points:
                if isinstance(value, MethodObject):
                    bound.append(value.deliver_into(receiver))
                elif isinstance(value, FunctionObject) and not value.ir.is_static_method:
                    bound.append(MethodObject(value.context, value.alloc_site,
                        value.container_scope, value.ir, current, receiver))
                else:
                    bound.append(value)
            return inherited.union(PointsToSet.from_objects(bound))
        return points

    binder = GuardNode(bind)
    state._add_points_flow_edge(PointerFlowEdge(binder, NormalNode(target), PointerFlowKind.NORMAL))

    def watch(binding, callback):
        def observe(_edge, points):
            for value in points:
                callback(value)
            # Observation installs further persistent edges; hierarchy objects
            # themselves are not candidate values of the requested attribute.
            return PointsToSet.empty()
        state._add_points_flow_edge(PointerFlowEdge(NormalNode(binding), GuardNode(observe), PointerFlowKind.NORMAL))
        # Some clients seed contextual variable facts directly before creating
        # a solver/PFG node. Replay the variable store too, not only node facts.
        for value in state.get_points_to(binding):
            callback(value)

    def attach(cls, force=False):
        if cls in attached or (cls == current and not force):
            return
        attached.add(cls)
        owner = cls.container_scope
        internal = state.get_internal_scope(cls)
        class_field = state.get_field(internal or owner, cls.context, cls, field)
        state._add_points_flow_edge(PointerFlowEdge(NormalNode(class_field), binder, PointerFlowKind.NORMAL))

    def reachable(start, relation):
        result, pending = set(), [start]
        while pending:
            cls = pending.pop()
            if cls not in result:
                result.add(cls)
                pending.extend(relation.get(cls, ()))
        return result

    def allow_collapsed_identity(cls):
        pending = [cls]
        while pending:
            candidate = pending.pop()
            if candidate not in ambiguous:
                ambiguous.add(candidate)
                attach(candidate, force=True)
                pending.extend(bases.get(candidate, ()))

    def base_arrived(parent, slot, base):
        if isinstance(base, ClassObject):
            edge = (parent, slot, base)
            if edge in observed_base_edges:
                return
            observed_base_edges.add(edge)
            bases.setdefault(parent, set()).add(base)
            slots = incoming_slots.setdefault(base, set())
            slots.add((parent, slot))
            if len(slots) > 1 or parent in ambiguous:
                # Sibling concrete types from one allocation site can collapse
                # to one object without forming a cycle. Multiple represented
                # base paths/slots must not turn equality into must-identity.
                residual["abstract_hierarchy_join_or_allocation_collapse"] = True
                allow_collapsed_identity(base)
            descendants = reachable(base, bases)
            if parent in descendants:
                # Same-site class allocation can collapse distinct concrete
                # classes into an abstract cycle. Equality is then no proof
                # that a provider is exactly the current/root concrete type.
                reverse = {}
                for cls, parents in bases.items():
                    for value in parents:
                        reverse.setdefault(value, set()).add(cls)
                cycle = descendants & reachable(parent, reverse)
                residual["abstract_hierarchy_cycle_or_allocation_collapse"] = True
                for cls in cycle:
                    allow_collapsed_identity(cls)
        visit(base)

    def visit(cls, include_own=True):
        if not isinstance(cls, ClassObject):
            residual["known_non_source_base_or_metaclass"] = True
            return
        owner = cls.container_scope
        if include_own:
            attach(cls)
        if cls in watched:
            return
        watched.add(cls)
        variables = getattr(cls.ir, "_kcfa_base_variables", None)
        if variables is None:
            variables = []
            for base in cls.ir.get_bases():
                name = getattr(base, "id", None)
                if name is None:
                    residual["unresolved_base_expression"] = True
                    continue
                variables.append(state._variable_factory.make_variable(name, VariableKind.GLOBAL))
        if getattr(cls.ir, "_kcfa_dynamic_bases", False) or any(
                variable.name.startswith("$dynamic_bases_") for variable in variables):
            residual["dynamic_base_slot_identity_unproved"] = True
            allow_collapsed_identity(cls)
        for index, variable in enumerate(variables):
            binding = state.get_variable(owner, owner.context, variable)
            watch(binding, lambda value, parent=cls, slot=index: base_arrived(parent, slot, value))

    # Preserve lexical-parent candidates for unbound and unknown receivers and
    # incomplete receiver relations. They are joined, never used for exclusion.
    visit(current, include_own=False)
    if isinstance(receiver, InstanceObject):
        visit(receiver.class_obj, include_own=False)
    elif isinstance(receiver, ClassObject):
        visit(receiver, include_own=False)
        # A class receiver can be an instance of a user metaclass. The metadata
        # edge must persist even when the metaclass arrives after this lookup.
        metadata = state.get_field(scope, context, receiver, Field(FieldKind.CLASS_METADATA, "metaclass"))
        watch(metadata, lambda meta: visit(meta, include_own=False))
