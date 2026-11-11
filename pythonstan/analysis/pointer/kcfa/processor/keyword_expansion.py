"""Persistent custom-mapping may-values for source-call ``**`` binding.

Native dictionary/unknown operand facts retain their existing transfer.  A
custom instance additionally projects keys()/type-level __getitem__ results
into a private native mapping consumed by the ordinary formal/collector rules.
This is not an argument-validity, ordering, exception, or presence proof.
"""
from ..constraints import AttrReadConstraint, AwaitConstraint, CallConstraint, ResidualEffectConstraint
from ..heap_model import elem, key, wildcard_key
from ..native_mapping import NATIVE_MAPPING_KEYS
from ..object import (
    AllocKind, AllocSite, AsyncGeneratorObject, BuiltinClassObject, BuiltinFunctionObject,
    BuiltinInstanceObject, BuiltinMethodObject, ClassObject, ConstantObject, CoroutineObject,
    DictObject, FunctionObject, GeneratorObject, InstanceObject, ListObject, ModuleObject,
    SetObject, SuperObject, TupleObject, is_summary_object,
)
from ..points_to_set import PointsToSet
from ..pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from ..variable import VariableKind
from .type_protocol import install_instance_method_call


_NONSTRING_NATIVE_FAMILIES = (
    ListObject, TupleObject, DictObject, SetObject, FunctionObject, ClassObject,
    ModuleObject, BuiltinClassObject, BuiltinFunctionObject, BuiltinMethodObject,
    GeneratorObject, AsyncGeneratorObject, CoroutineObject, SuperObject,
)
_NONSTRING_BUILTIN_TYPES = frozenset({
    "list", "tuple", "dict", "set", "frozenset", "int", "bool", "float", "complex",
    "bytes", "bytearray", "memoryview", "range", "iterator", "enumerate", "zip", "map",
    "filter", "reversed", "dict_keys", "dict_values", "dict_items", "coroutine_iterator",
    "async_generator_asend",
})


def _observe(state, source, callback):
    def observe(_edge, points):
        callback(points)
        return PointsToSet.empty()

    state._add_points_flow_edge(PointerFlowEdge(
        NormalNode(source), GuardNode(observe), PointerFlowKind.NORMAL))


class KeywordExpansions:
    def __init__(self, solver):
        self.solver = solver
        self.operands = {}
        self.receivers = set()
        self.next_variable = 0
        self.partition_buckets = {}
        self.summary_partitions = {}

    def _variable(self, purpose):
        self.next_variable += 1
        return self.solver.variable_factory.make_variable(
            f"$keyword_expansion@{self.next_variable}@{purpose}", VariableKind.TEMPORARY)

    def install(self, scope, mapping, call_site):
        identity = (scope, mapping, call_site)
        existing = self.operands.get(identity)
        if existing is not None:
            return existing
        solver, state = self.solver, self.solver.state
        output = self._variable("mapping")
        self.operands[identity] = output
        source = state.get_variable(scope, scope.context, mapping)
        destination = state.get_variable(scope, scope.context, output)
        # Preserve every prior native/opaque operand transfer. The additional
        # projection never mutates the source variable's points-to relation.
        state._add_var_points_flow(source, destination)

        def mappings_added(points):
            for receiver in points:
                if isinstance(receiver, InstanceObject):
                    self._project(scope, mapping, call_site, receiver, destination)

        _observe(state, source, mappings_added)
        return output

    def _project(self, scope, mapping, call_site, receiver, destination):
        identity = (scope, mapping, call_site, receiver)
        if identity in self.receivers:
            return
        self.receivers.add(identity)  # guard before replay installs more flows
        solver, state = self.solver, self.solver.state
        solver.add_constraint(scope, scope.context, ResidualEffectConstraint(
            str(call_site),
            "Custom keyword mapping retains keys/getitem may-values; key string validity, "
            "presence, duplicate rejection, descriptor precedence, iteration order, "
            "exceptions and callback reentry remain open",
            ("keyword_mapping_protocol", "argument_presence", "argument_collision",
             "key_string_validity", "keyword_key_result_correlation",
             "may_invoke_callbacks", "may_mutate_heap", "may_raise"),
        ))
        if call_site is None:
            return
        site = AllocSite(("keyword_expansion", call_site, mapping, receiver), AllocKind.DICT)
        projected = DictObject(state.select_allocation_context(scope.context, site, identity="alias"), site)
        state._heap.set_obj(scope, scope.context, site, projected)
        state.obj_scope[projected] = scope
        solver.handle_new_points_to(destination, scope, PointsToSet.singleton(projected))
        bound = self._variable("receiver")
        solver.handle_new_points_to(state.get_variable(scope, scope.context, bound),
                                   scope, PointsToSet.singleton(receiver))
        method, iterable, keys = (self._variable(purpose) for purpose in ("keys_method", "iterable", "key"))
        # keys is an ordinary attribute: instance shadowing/interceptors and
        # descriptor results use the established attribute semantics.
        solver.add_constraint(scope, scope.context, AttrReadConstraint(bound, "keys", method, call_site))
        solver.add_constraint(scope, scope.context, CallConstraint(method, (), frozenset(), iterable, call_site))
        # Consume old and future iterable elements, activating native generator
        # or custom iterator bodies. Completion values are not keyword names.
        solver.add_constraint(scope, scope.context, AwaitConstraint(iterable, keys, call_site, "delegate"))
        seen = set()

        def keys_added(points):
            for name in points:
                if name in seen:
                    continue
                seen.add(name)
                self._getitem(scope, call_site, receiver, projected, name)

        _observe(state, state.get_variable(scope, scope.context, keys), keys_added)

    def _getitem(self, scope, call_site, receiver, projected, name):
        solver, state = self.solver, self.solver.state
        argument, result = self._variable("getitem_key"), self._variable("getitem_result")
        solver.handle_new_points_to(state.get_variable(scope, scope.context, argument),
                                   scope, PointsToSet.singleton(name))
        # Python invokes this implicit special method through the type, even
        # when the instance contains a shadow named __getitem__.
        install_instance_method_call(solver, scope, receiver, "__getitem__",
                                     (argument,), result, call_site,
                                     semantic_partition=self._partition(scope, call_site, receiver, name))
        exact_constant = isinstance(name, ConstantObject) and not is_summary_object(name)
        if exact_constant and type(name.value) is str:
            field = key(name.value)
        elif (exact_constant and type(name.value) in (bool, int, float, complex, bytes, type(None))
              or isinstance(name, _NONSTRING_NATIVE_FAMILIES)
              or isinstance(name, BuiltinInstanceObject) and name.builtin_type in _NONSTRING_BUILTIN_TYPES):
            # CPython can execute getitem before rejecting a non-string key.
            # Keep those effects without fabricating a valid named argument.
            return
        else:
            field = wildcard_key()
        values = state.get_variable(scope, scope.context, result)
        state._add_var_points_flow(values, state.get_field(scope, scope.context, projected, field))
        state._add_var_points_flow(values, state.get_field(scope, scope.context, projected, elem()))
        provenance = state.get_field(scope, scope.context, projected, NATIVE_MAPPING_KEYS)

        def value_added(points):
            if not points.is_empty():
                solver.handle_new_points_to(provenance, scope, PointsToSet.singleton(name))

        _observe(state, values, value_added)

    def _partition(self, scope, call_site, receiver, name):
        if self.solver.config.keyword_context_model == "none":
            return None
        from pythonstan.ir.ir_statements import IRStatement
        from ..protocol_context import KeywordPartition
        # Never retain synthetic allocation recipes: they can contain owner
        # objects, caller scopes, and recursive contexts. A fresh source-only
        # AllocSite contains exactly the source IR identity and allocation kind.
        allocation = receiver.alloc_site
        source = (AllocSite(allocation.stmt, allocation.kind)
                  if isinstance(allocation.stmt, IRStatement) else None)
        operation = (call_site, source)
        bucket = self.partition_buckets.setdefault(operation, {})
        exact = (source is not None and isinstance(name, ConstantObject)
                 and type(name.value) is str and not is_summary_object(name))
        if exact:
            if name.value in bucket:
                return bucket[name.value]
            if len(bucket) < self.solver.config.keyword_context_limit:
                token = KeywordPartition(call_site, source, "exact", name.value)
                bucket[name.value] = token
                return token
        token = self.summary_partitions.get(operation)
        if token is None:
            token = self.summary_partitions[operation] = KeywordPartition(call_site, source, "summary", None)
        self.solver.add_constraint(scope, scope.context, ResidualEffectConstraint(
            str(call_site),
            "Keyword receiver/key context uses a joined bucket for opaque allocation/key "
            "or exact-key budget overflow; all may-values remain and key/result correlation is unproved",
            ("keyword_key_result_correlation", "keyword_partition_join", "may_invoke_callbacks", "may_raise"),
        ))
        return token


def install_keyword_expansion(solver, scope, mapping, call_site):
    manager = getattr(solver, "_keyword_expansions", None)
    if manager is None:
        manager = solver._keyword_expansions = KeywordExpansions(solver)
    return manager.install(scope, mapping, call_site)
