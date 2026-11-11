"""Persistent native-dictionary key callback may-relations.

The private key slot is separate from Python dictionary values/items.  It is
not a key-presence or insertion-order fact.  Dispatch deliberately keeps both
equality directions; collision/precedence/exception/reentry are obligations.
"""
from dataclasses import dataclass
import ast
from collections import deque

from .constraints import Constraint, CallConstraint, LoadConstraint, ResidualEffectConstraint, TruthTestConstraint
from .heap_model import Field, FieldKind, attr, elem, key
from .object import BuiltinInstanceObject, ClassObject, ConstantObject, DictObject, FunctionObject, InstanceObject, TupleObject, is_summary_object
from .points_to_set import PointsToSet
from .pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from .variable import VariableKind


NATIVE_MAPPING_KEYS = Field(FieldKind.CLASS_METADATA, "native_mapping_keys")
NATIVE_MAPPING_NONSCALAR_KEYS = Field(FieldKind.CLASS_METADATA, "native_mapping_nonscalar_keys")
NATIVE_MAPPING_KEY_FIELDS = (NATIVE_MAPPING_KEYS, NATIVE_MAPPING_NONSCALAR_KEYS)


@dataclass(frozen=True)
class NativeMappingProtocolConstraint(Constraint):
    base: object
    index: object
    call_site: object
    store_key: bool = False
    operation: str = "lookup"

    def variables(self):
        return {self.base, self.index}

    def __str__(self):
        return f"NativeMappingProtocolConstraint: {self.operation}@{self.call_site}"


def exact_native_mapping(obj):
    return isinstance(obj, DictObject) or (
        isinstance(obj, BuiltinInstanceObject) and obj.builtin_type == "dict")


def exact_scalar_key(obj):
    return (isinstance(obj, ConstantObject)
            and type(obj.value) in (bool, int, float, complex, str, bytes, type(None)))


def _observe(state, source, callback):
    def observe(_edge, points):
        callback(points)
        return PointsToSet.empty()
    gate = GuardNode(observe)
    state._add_points_flow_edge(PointerFlowEdge(NormalNode(source), gate, PointerFlowKind.NORMAL))


class NativeMappingProtocols:
    def __init__(self, solver):
        self.solver = solver
        self.installed = set()
        self.bound_variables = {}
        self.metaclass_calls = set()
        self.classified_mappings = set()
        self.tuple_protocols = set()
        self._tuple_pending = deque()
        self._tuple_draining = False
        self.statistics = {"classified_key_objects": 0, "key_query_objects": 0,
                           "equality_candidate_pairs": 0, "all_key_observers": 0,
                           "tuple_hash_observers": 0, "tuple_hash_component_objects": 0,
                           "tuple_equality_observers": 0, "tuple_equality_candidate_pairs": 0}

    def _classify_mapping(self, scope, mapping):
        """Classify each stored key once per mapping, never per query/store."""
        if mapping in self.classified_mappings:
            return
        self.classified_mappings.add(mapping)
        state = self.solver.state
        source = state.get_field(scope, scope.context, mapping, NATIVE_MAPPING_KEYS)
        target = state.get_field(scope, scope.context, mapping, NATIVE_MAPPING_NONSCALAR_KEYS)
        observed = set()
        def classify(_edge, points):
            fresh = [obj for obj in points if obj not in observed]
            observed.update(fresh)
            self.statistics["classified_key_objects"] += len(fresh)
            return PointsToSet.from_objects(obj for obj in fresh if not exact_scalar_key(obj))
        gate = GuardNode(classify)
        state._add_points_flow_edge(PointerFlowEdge(NormalNode(source), gate, PointerFlowKind.NORMAL))
        state._add_points_flow_edge(PointerFlowEdge(gate, NormalNode(target), PointerFlowKind.NORMAL))

    def _bound(self, scope, constraint, obj):
        identity = (scope, constraint.call_site, obj)
        variable = self.bound_variables.get(identity)
        if variable is None:
            variable = self.solver.variable_factory.make_variable(
                f"$native_key@{constraint.call_site.site_id}@{id(obj)}", VariableKind.TEMPORARY)
            self.bound_variables[identity] = variable
            contextual = self.solver.state.get_variable(scope, scope.context, variable)
            self.solver.handle_new_points_to(contextual, scope, PointsToSet.singleton(obj))
        return variable

    def _residual(self, scope, constraint):
        self.solver.add_constraint(scope, scope.context, ResidualEffectConstraint(
            str(constraint.call_site),
            "Native dictionary key may-calls retain hash/equality candidates; hash validity, collisions, "
            "NotImplemented/precedence, descriptor binding, unknown keys, exceptions and callback reentry remain open",
            ("native_mapping_key_protocol", "hash_result_validity", "equality_dispatch", "exception_routing",
             "may_invoke_callbacks", "may_mutate_heap", "may_raise"),
        ))

    def _method(self, scope, constraint, receiver, method, others=()):
        if exact_scalar_key(receiver):
            return
        self._residual(scope, constraint)
        if isinstance(receiver, TupleObject):
            # Native tuple equality produces a builtin bool. Only its element
            # rich-comparison results need the source truth-callback transfer;
            # do not allocate an empty Python-call/truth subscription per tuple
            # pair, especially for primitive-only component relations.
            if method == "__hash__":
                self._tuple_protocol(scope, constraint, receiver, method)
            elif method == "__eq__" and len(others) == 1 and isinstance(others[0], TupleObject):
                self._tuple_protocol(scope, constraint, receiver, method, others)
            return
        arguments = tuple(self._bound(scope, constraint, other) for other in others)
        result = None
        if method == "__eq__":
            result = self.solver.variable_factory.make_variable(
                f"$native_equality@{constraint.call_site.site_id}@{id(receiver)}@"
                + ":".join(str(id(obj)) for obj in others), VariableKind.TEMPORARY)
            self.solver.add_constraint(scope, scope.context, TruthTestConstraint(result, constraint.call_site))
        if isinstance(receiver, InstanceObject):
            # Shared persistent type-level source-function gate.  No instance
            # shadow or absent method fabricates a native invocation.
            from .processor.type_protocol import install_instance_method_call
            install_instance_method_call(self.solver, scope, receiver, method,
                                         arguments, result, constraint.call_site)
        elif isinstance(receiver, ClassObject):
            self._metaclass_method(scope, constraint, receiver, method, arguments, result)

    @staticmethod
    def _tuple_arity(receiver):
        """Known literal arity, never inferred from currently nonempty slots."""
        statement = receiver.alloc_site.stmt
        node = statement.get_ast() if hasattr(statement, "get_ast") else None
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            node = node.value
        if isinstance(node, ast.Tuple) and not any(isinstance(item, ast.Starred) for item in node.elts):
            return len(node.elts)
        return None

    def _tuple_fields(self, receiver):
        # Literal producers have a fixed set of positions. Subscribe before
        # their values appear, as well as to their aggregate element relation.
        # tuple(iterable) and other modeled producers publish through elem().
        fields = {elem(): None}
        arity = self._tuple_arity(receiver)
        if arity is not None and self.solver.config.index_sensitive:
            for index in range(arity):
                fields[key(index)] = None
                fields[Field(FieldKind.POSITION, index=index)] = None
        for field in self.solver.state.known_fields_for_object(receiver):
            if field.kind in (FieldKind.ELEMENT, FieldKind.POSITION) or (
                    field.kind == FieldKind.KEY and field.key_type == "number"):
                fields[field] = None
        return tuple(fields)

    def _tuple_protocol(self, scope, constraint, receiver, method, others=()):
        identity = (scope, constraint.call_site, receiver, method, others)
        if identity in self.tuple_protocols:
            return
        # Mark before replay: abstract allocation graphs can contain cycles.
        self.tuple_protocols.add(identity)
        self._tuple_pending.append((scope, constraint, receiver, method, others))
        if self._tuple_draining:
            return
        self._tuple_draining = True
        try:
            # Observation installation replays synchronously. This finite
            # discovery trampoline prevents nested tuples exhausting Python's
            # stack; subsequent field growth uses the ordinary solver queues.
            while self._tuple_pending:
                scope, constraint, receiver, method, others = self._tuple_pending.popleft()
                if method == "__hash__":
                    self._install_tuple_hash(scope, constraint, receiver)
                elif method == "__eq__":
                    self._install_tuple_equality(scope, constraint, receiver, others[0])
        finally:
            self._tuple_draining = False

    def _install_tuple_hash(self, scope, constraint, receiver):
        self.statistics["tuple_hash_observers"] += 1
        observed = set()

        def components_added(points):
            for component in points:
                if component in observed:
                    continue
                observed.add(component)
                self.statistics["tuple_hash_component_objects"] += 1
                self._method(scope, constraint, component, "__hash__")

        state = self.solver.state
        for field in self._tuple_fields(receiver):
            source = state.get_field(scope, scope.context, receiver, field)
            _observe(state, source, components_added)

    def _install_tuple_equality(self, scope, constraint, left, right):
        """Persistent binary may-join, with no snapshot-only scalar pruning."""
        self.statistics["tuple_equality_observers"] += 1
        left_arity, right_arity = self._tuple_arity(left), self._tuple_arity(right)
        indexed = (self.solver.config.index_sensitive and left_arity is not None
                   and right_arity is not None and not is_summary_object(left)
                   and not is_summary_object(right))
        if indexed:
            # Different lengths still compare their common prefix first.
            groups = [((key(index), Field(FieldKind.POSITION, index=index)),
                       (key(index), Field(FieldKind.POSITION, index=index)))
                      for index in range(min(left_arity, right_arity))]
        else:
            # Unknown shapes/index-insensitive/summary tuple representations
            # cannot justify aligning positions. Keep the aggregate may-join.
            groups = [(self._tuple_fields(left), self._tuple_fields(right))]
        self.solver.add_constraint(scope, scope.context, ResidualEffectConstraint(
            str(constraint.call_site),
            "Native tuple comparison keeps aligned literal positions or an aggregate may-join; "
            "prefix truth, identity, NotImplemented, exceptions and producer coverage remain open",
            ("native_tuple_protocol", "tuple_comparison", "may_invoke_callbacks", "may_mutate_heap", "may_raise"),
        ))
        for left_fields, right_fields in groups:
            self._tuple_equality_group(scope, constraint, left, right, left_fields, right_fields)

    def _tuple_equality_group(self, scope, constraint, left, right, left_fields, right_fields):
        # Do not enumerate primitive x primitive pairs: exact primitive payloads
        # cannot run Python hooks. Classification is per arriving object, so a
        # later custom component always joins all earlier opposite-side values.
        left_all, right_all, left_custom, right_custom = set(), set(), set(), set()

        def compare(left_value, right_value):
            self.statistics["tuple_equality_candidate_pairs"] += 1
            self._method(scope, constraint, left_value, "__eq__", (right_value,))
            self._method(scope, constraint, right_value, "__eq__", (left_value,))

        def left_added(points):
            for obj in points:
                if obj in left_all:
                    continue
                left_all.add(obj)
                if exact_scalar_key(obj):
                    for other in tuple(right_custom):
                        compare(obj, other)
                else:
                    left_custom.add(obj)
                    for other in tuple(right_all):
                        compare(obj, other)

        def right_added(points):
            for obj in points:
                if obj in right_all:
                    continue
                right_all.add(obj)
                if exact_scalar_key(obj):
                    for other in tuple(left_custom):
                        compare(other, obj)
                else:
                    right_custom.add(obj)
                    for other in tuple(left_all):
                        compare(other, obj)

        state = self.solver.state
        for field in left_fields:
            _observe(state, state.get_field(scope, scope.context, left, field), left_added)
        for field in right_fields:
            _observe(state, state.get_field(scope, scope.context, right, field), right_added)

    def _metaclass_method(self, scope, constraint, receiver, method, arguments, result):
        identity = (scope, constraint.call_site, receiver, method, arguments, result)
        if identity in self.metaclass_calls:
            return
        self.metaclass_calls.add(identity)
        from .processor.class_protocol import METACLASS_FIELD
        solver, state = self.solver, self.solver.state
        suffix = f"{constraint.call_site.site_id}@{id(receiver)}@{method}"
        receiver_var = self._bound(scope, constraint, receiver)
        meta_var = solver.variable_factory.make_variable(f"$native_key_metaclass@{suffix}", VariableKind.TEMPORARY)
        observed = solver.variable_factory.make_variable(f"$native_key_meta_observed@{suffix}", VariableKind.TEMPORARY)
        active = solver.variable_factory.make_variable(f"$native_key_meta_active@{suffix}", VariableKind.TEMPORARY)
        state._add_var_points_flow(state.get_field(scope, scope.context, receiver, METACLASS_FIELD),
                                   state.get_variable(scope, scope.context, meta_var))
        solver.add_constraint(scope, scope.context, LoadConstraint(meta_var, attr(method), observed))
        invocation = CallConstraint(active, (receiver_var, *arguments), frozenset(), result, constraint.call_site)
        registered = False
        def activate(_edge, points):
            nonlocal registered
            functions = [obj for obj in points if isinstance(obj, FunctionObject)]
            if functions and not registered:
                registered = True
                solver.add_constraint(scope, scope.context, invocation)
            return PointsToSet.from_objects(functions)
        gate = GuardNode(activate)
        state._add_points_flow_edge(PointerFlowEdge(
            NormalNode(state.get_variable(scope, scope.context, observed)), gate, PointerFlowKind.NORMAL))
        state._add_points_flow_edge(PointerFlowEdge(
            gate, NormalNode(state.get_variable(scope, scope.context, active)), PointerFlowKind.NORMAL))

    def apply(self, scope, constraint):
        state = self.solver.state
        for mapping in state.get_points_to(state.get_variable(scope, scope.context, constraint.base)):
            if not exact_native_mapping(mapping):
                continue
            identity = (scope, constraint, mapping)
            if identity in self.installed:
                continue
            self.installed.add(identity)
            self._install(scope, constraint, mapping)

    def _install(self, scope, constraint, mapping):
        # Each mapping gets its own closure variables; a union base must never
        # alias the key sets of a different mapping's observation callbacks.
        state = self.solver.state
        query = state.get_variable(scope, scope.context, constraint.index)
        stored = state.get_field(scope, scope.context, mapping, NATIVE_MAPPING_KEYS)
        nonscalar_stored = state.get_field(scope, scope.context, mapping, NATIVE_MAPPING_NONSCALAR_KEYS)
        self._classify_mapping(scope, mapping)
        scalar_queries, custom_queries, all_keys, custom_keys = set(), set(), set(), set()
        watching_all_keys = False
        def pair(left, right):
            self.statistics["equality_candidate_pairs"] += 1
            self._method(scope, constraint, left, "__eq__", (right,))
            self._method(scope, constraint, right, "__eq__", (left,))
        def all_stored_added(points):
            for obj in points:
                if obj in all_keys:
                    continue
                all_keys.add(obj)
                for other in tuple(custom_queries):
                    pair(obj, other)
        def query_added(points):
            nonlocal watching_all_keys
            for obj in points:
                if obj in scalar_queries or obj in custom_queries:
                    continue
                self.statistics["key_query_objects"] += 1
                if exact_scalar_key(obj):
                    scalar_queries.add(obj)
                    for other in tuple(custom_keys):
                        pair(other, obj)
                    continue
                custom_queries.add(obj)
                self._method(scope, constraint, obj, "__hash__")
                if not watching_all_keys:
                    # A scalar query can only invoke user equality on a custom
                    # stored key. Subscribe to ALL keys only when a custom or
                    # unknown query actually arrives; replay keeps older keys.
                    watching_all_keys = True
                    self.statistics["all_key_observers"] += 1
                    _observe(state, stored, all_stored_added)
                else:
                    for other in tuple(all_keys):
                        pair(other, obj)
        def stored_added(points):
            for obj in points:
                if obj in custom_keys:
                    continue
                custom_keys.add(obj)
                for other in tuple(scalar_queries):
                    pair(obj, other)
        # Both observations replay existing facts and observe future ones.
        _observe(state, query, query_added)
        _observe(state, nonscalar_stored, stored_added)
        if constraint.store_key:
            state._add_var_points_flow(query, stored)


def apply_native_mapping_protocol(solver, scope, constraint):
    manager = getattr(solver.state, "_native_mapping_protocols", None)
    if manager is None:
        manager = solver.state._native_mapping_protocols = NativeMappingProtocols(solver)
    manager.apply(scope, constraint)


def copy_native_mapping_keys(state, scope, source, target, excluded=frozenset()):
    """Persistent may-key identities, excluding only definitely consumed names.

    The caller installs this once per mapping-copy relation. Unknown/summary
    keys may denote other names, so only exact nonsummary string constants can
    be removed. Neither metadata field establishes concrete key presence.
    """
    if exact_native_mapping(source) and exact_native_mapping(target):
        for field in NATIVE_MAPPING_KEY_FIELDS:
            source_slot = state.get_field(scope, scope.context, source, field)
            target_slot = state.get_field(scope, scope.context, target, field)
            if not excluded:
                state._add_var_points_flow(source_slot, target_slot)
                continue

            def unconsumed(_edge, points):
                return PointsToSet.from_objects(obj for obj in points
                    if not (isinstance(obj, ConstantObject)
                            and not is_summary_object(obj)
                            and type(obj.value) is str
                            and obj.value in excluded))

            guard = GuardNode(unconsumed)
            state._add_points_flow_edge(PointerFlowEdge(
                NormalNode(source_slot), guard, PointerFlowKind.NORMAL))
            state._add_points_flow_edge(PointerFlowEdge(
                guard, NormalNode(target_slot), PointerFlowKind.NORMAL))
