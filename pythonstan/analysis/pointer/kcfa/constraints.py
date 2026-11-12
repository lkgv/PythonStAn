"""Pointer constraints and constraint management.

This module defines the constraint types used in the pointer analysis and
provides efficient storage and indexing for constraint-based solving.
"""

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union, override

from .context import Ctx, Scope
from .variable import FieldAccess, Variable

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRStatement

    from .context import CallSite
    from .heap_model import Field
    from .object import AbstractObject, AllocSite
    from .pointer_flow_graph import SelectorNode
    from .protocol_context import KeywordPartition
    from .variable import FieldAccess, Variable

__all__ = [
    "Constraint",
    "ResidualEffectConstraint",
    "TruthTestConstraint",
    "OperatorProtocolConstraint",
    "SpecialMethodLookupConstraint",
    "CopyConstraint",
    "MappingCopyConstraint",
    "InheritanceConstraint",
    "LoadConstraint",
    "AttrReadConstraint",
    "AttrWriteConstraint",
    "StoreConstraint",
    "AllocConstraint",
    "LoadSubscrConstraint",
    "StoreSubscrConstraint",
    "CallConstraint",
    "ReturnConstraint",
    "SuperResolveConstraint",
    "YieldConstraint",
    "AwaitConstraint",
    "ConstraintManager",
]


class Constraint(ABC):
    """Base class for all pointer constraints.

    Constraints represent facts about pointer flow in the program.
    """

    @abstractmethod
    def variables(self) -> set["Variable"]:
        """Get all variables involved in this constraint.

        Returns:
            Set of variables
        """

    @abstractmethod
    @override
    def __str__(self) -> str:
        """String representation for debugging."""


@dataclass(frozen=True)
class ResidualEffectConstraint(Constraint):
    """An obligation the current abstraction does not discharge.

    It is retained independently of optional diagnostic tracking. It adds no
    fabricated call edges and must not be treated as a semantic top transfer.
    """

    location: str
    reason: str
    effects: tuple[str, ...]

    @override
    def variables(self) -> set[Variable]:
        return set()

    @override
    def __str__(self) -> str:
        return f"ResidualEffectConstraint: {self.location}: {self.reason}"


@dataclass(frozen=True)
class TruthTestConstraint(Constraint):
    """Subscribe to implicit type-level truth callback effects of a value."""

    base: "Variable"
    call_site: "CallSite"

    @override
    def variables(self) -> set[Variable]:
        return {self.base}

    @override
    def __str__(self) -> str:
        return f"TruthTestConstraint: truth({self.base}) at {self.call_site}"


@dataclass(frozen=True)
class OperatorProtocolConstraint(Constraint):
    """One type-level arithmetic method candidate with explicit operands."""

    base: "Variable"
    method: str
    arguments: tuple["Variable", ...]
    target: Optional["Variable"]
    call_site: "CallSite"

    @override
    def variables(self) -> set[Variable]:
        return {self.base, *self.arguments}

    @override
    def __str__(self) -> str:
        return f"OperatorProtocolConstraint: type({self.base}).{self.method}"


@dataclass(frozen=True)
class SpecialMethodLookupConstraint(Constraint):
    """Read an ordinary special method from the receiver's type, unbound.

    Unlike instance attribute access, this bypasses instance fields/hooks.
    Custom descriptor binding and non-instance receiver types remain residual.
    """

    base: "Variable"
    method: str
    target: "Variable"

    @override
    def variables(self) -> set[Variable]:
        return {self.base, self.target}

    @override
    def __str__(self) -> str:
        return f"SpecialMethodLookupConstraint: {self.target} = type({self.base}).{self.method}"


@dataclass(frozen=True)
class CopyConstraint(Constraint):
    """Copy constraint: target = source.

    Represents assignment from one variable to another.

    Attributes:
        source: Source variable
        target: Target variable
    """

    source: "Variable"
    target: "Variable"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.source, self.target}

    @override
    def __str__(self) -> str:
        return f"CopyConstraint: {self.target} = {self.source}"


@dataclass(frozen=True)
class MappingCopyConstraint(Constraint):
    """Persistently copy mapping fields into a distinct, already known object.

    Used for **kwargs collection. Excluded finite names were consumed by
    named parameters; unknown-key values remain conservatively included.
    """

    source: "Variable"
    target_object: "AbstractObject"
    excluded_keys: frozenset[str] = frozenset()

    @override
    def variables(self) -> set[Variable]:
        return {self.source}

    @override
    def __str__(self) -> str:
        return f"MappingCopyConstraint: {self.target_object} <- fields({self.source})"


@dataclass(frozen=True)
class LoadConstraint(Constraint):
    """Load constraint: target = base.field or target = base[index].

    Represents loading a field from an object or subscript access.

    Attributes:
        base: Base variable pointing to objects
        field: Field being loaded (for attribute access, None for subscript)
        target: Target variable receiving field value
        index: Index variable for subscript access (None for attribute access)
    """

    base: "Variable"
    field: Optional["Field"]
    target: "Variable"
    index: Optional["Variable"] = None

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        vars = {self.base}
        if self.index:
            vars.add(self.index)
        return vars

    @override
    def __str__(self) -> str:
        if self.index:
            return f"LoadConstraint: {self.target} = {self.base}[{self.index}]"
        return f"LoadConstraint: {self.target} = {self.base}{self.field}"


@dataclass(frozen=True)
class AttrReadConstraint(Constraint):
    """Attribute read constraint: target = base.attr.

    Represents attribute reads that require descriptor/interceptor semantics.

    Attributes:
        base: Base variable pointing to objects
        attr: Attribute name or field key
        target: Target variable receiving the attribute value
        call_site: Call site metadata for context-sensitive processing
    """

    base: "Variable"
    attr: Union[str, "Field"]
    target: "Variable"
    call_site: "CallSite"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.base, self.target}

    @override
    def __str__(self) -> str:
        attr_str = f".{self.attr}" if isinstance(self.attr, str) else str(self.attr)
        return f"AttrReadConstraint: {self.target} = {self.base}{attr_str}"


@dataclass(frozen=True)
class AttrWriteConstraint(Constraint):
    """Attribute write constraint: base.attr = source.

    Represents attribute writes that require descriptor/interceptor semantics.

    Attributes:
        base: Base variable pointing to objects
        attr: Attribute name or field key
        source: Source variable being stored
        call_site: Call site metadata for context-sensitive processing
    """

    base: "Variable"
    attr: Union[str, "Field"]
    source: "Variable"
    call_site: "CallSite"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.base, self.source}

    @override
    def __str__(self) -> str:
        attr_str = f".{self.attr}" if isinstance(self.attr, str) else str(self.attr)
        return f"AttrWriteConstraint: {self.base}{attr_str} = {self.source}"


@dataclass(frozen=True)
class InheritanceConstraint(Constraint):
    """Inheritance constraint: target.obj.class(.., base, ...) and base.field -> target

    Represents loading a field inherit from the index-th base class.

    Attributes:
        base: Base variable pointing to base class objects
        field: Field being loaded
        target: Target field access
        index: Index of the base class in the inheritance chain
    """

    base: "Variable"
    field: Optional["Field"]
    target: "SelectorNode"
    index: int = 0

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.base}

    @override
    def __str__(self) -> str:
        return f"InheritanceConstraint: {self.base}.{self.field} -[{self.index}]-> {self.target}"


@dataclass(frozen=True)
class SuperResolveConstraint(Constraint):
    """Constraint to resolve super() arguments and populate SuperObject.

    This constraint is triggered when super() result's points-to set contains
    a SuperObject allocation. It resolves the class and instance arguments:

    - Explicit: super(Class, obj) - resolve from provided variables
    - Implicit: super() - resolve from __class__ cell var and first parameter

    Once resolved, updates the SuperObject with current_class and instance_obj.

    Attributes:
        target: Variable pointing to SuperObject allocation
        class_var: Variable for class argument (None for implicit)
        instance_var: Variable for instance argument (None for implicit)
        implicit: True if super() called without arguments
    """

    target: "Variable"
    class_var: Optional["Variable"]
    instance_var: Optional["Variable"]
    implicit: bool = False

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        vars = {self.target}
        if self.class_var:
            vars.add(self.class_var)
        if self.instance_var:
            vars.add(self.instance_var)
        return vars

    @override
    def __str__(self) -> str:
        if self.implicit:
            return f"SuperResolveConstraint: {self.target} = super() (implicit)"
        return (
            f"SuperResolveConstraint: {self.target} = super({self.class_var}, {self.instance_var})"
        )


@dataclass(frozen=True)
class LoadSubscrConstraint(Constraint):
    """Load constraint: target = base.field or target = base[index].

    Represents loading a field from an object or subscript access.

    Attributes:
        base: Base variable pointing to objects
        field: Field being loaded (for attribute access, None for subscript)
        target: Target variable receiving field value
        index: Index variable for subscript access (None for attribute access)
    """

    target: "Variable"
    base: "Variable"
    index: "Variable"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.index}

    @override
    def __str__(self) -> str:
        return f"LoadSubscrConstraint: {self.target} = {self.base}[{self.index}]"


@dataclass(frozen=True)
class StoreConstraint(Constraint):
    """Store constraint: base.field = source or base[index] = source.

    Represents storing to a field of an object or subscript access.

    Attributes:
        base: Base variable pointing to objects
        field: Field being stored to (for attribute access, None for subscript)
        source: Source variable being stored
        index: Index variable for subscript access (None for attribute access)
    """

    base: "Variable"
    field: Optional["Field"]
    source: "Variable"
    index: Optional["Variable"] = None
    summary_only: bool = False

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.base}

    @override
    def __str__(self) -> str:
        if self.index:
            return f"StoreConstraint: {self.base}[{self.index}] = {self.source}"
        return f"StoreConstraint: {self.base}{self.field} = {self.source}"


@dataclass(frozen=True)
class StoreSubscrConstraint(Constraint):
    """Store constraint: base.field = source or base[index] = source.

    Represents storing to a field of an object or subscript access.

    Attributes:
        base: Base variable pointing to objects
        field: Field being stored to (for attribute access, None for subscript)
        source: Source variable being stored
        index: Index variable for subscript access (None for attribute access)
    """

    base: "Variable"
    index: "Variable"
    source: "Variable"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        vars = {self.index}
        if self.index:
            vars.add(self.index)
        return vars

    @override
    def __str__(self) -> str:
        return f"StoreSubscrConstraint: {self.base}[{self.index}] = {self.source}"


@dataclass(frozen=True)
class AllocConstraint(Constraint):
    """Allocation constraint: target = new Object.

    Represents object allocation.

    Attributes:
        target: Target variable receiving new object
        alloc_site: Allocation site of new object
    """

    target: "Variable"
    alloc_site: "AllocSite"

    def __post_init__(self) -> None:
        from pythonstan.ir.ir_statements import IRStatement

        from .object import AllocSite

        assert isinstance(self.alloc_site, AllocSite), (
            f"alloc_site must be an AllocSite, but got {type(self.alloc_site)}"
        )
        assert isinstance(self.alloc_site.stmt, IRStatement), (
            f"alloc_site.stmt must be an IRStatement, but got {type(self.alloc_site.stmt)}"
        )

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.target}

    @override
    def __str__(self) -> str:
        return f"AllocConstraint: {self.target} = new {self.alloc_site}"


@dataclass(frozen=True)
class CallConstraint(Constraint):
    """Call constraint: target = callee(args...).

    Represents function call. Actual call edges are resolved dynamically
    based on what callee points to.

    Attributes:
        callee: Variable holding callable object
        args: Tuple of argument variables
        target: Optional target variable for return value
        call_site: Call site metadata bound to an IR statement
    """

    callee: "Variable"
    args: tuple["Variable", ...]
    kwargs: frozenset[tuple[str, "Variable"]]
    target: Optional["Variable"]
    call_site: "CallSite"
    semantic_partition: Optional["KeywordPartition"] = None

    @property
    def stmt(self) -> "IRStatement":
        return self.call_site.statement

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        vars = {self.callee, *self.args}
        vars.update(var for _, var in self.kwargs)
        if self.target:
            vars.add(self.target)
        return vars

    @override
    def __str__(self) -> str:
        args_str = ", ".join(str(a) for a in self.args)
        if self.target:
            return f"{self.target} = {self.callee}({args_str})"
        return f"CallConstraint: {self.callee}({args_str})"


@dataclass(frozen=True)
class ReturnConstraint(Constraint):
    """Return constraint: connect callee return to caller target.

    Represents flow of return value from callee to caller.

    Attributes:
        callee_return: Special $return variable in callee
        caller_target: Target variable in caller
    """

    callee_return: "Variable"
    caller_target: "Variable"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        return {self.callee_return, self.caller_target}

    @override
    def __str__(self) -> str:
        return f"ReturnConstraint: {self.caller_target} = return({self.callee_return})"


@dataclass(frozen=True)
class YieldConstraint(Constraint):
    """Yield constraint: [target =] yield value.

    Represents yielding a value from a generator function. The yielded value
    flows to the generator object's elem() field. If a target is present,
    the value sent to the generator flows into the target.

    Attributes:
        value: Variable holding the yielded value (or None for bare yield)
        target: Variable to receive sent value (for target = yield value)
        generator_var: Variable holding the current generator object ($generator)
    """

    value: Optional["Variable"]
    target: Optional["Variable"]
    generator_var: "Variable"

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        vars = {self.generator_var}
        if self.value:
            vars.add(self.value)
        if self.target:
            vars.add(self.target)
        return vars

    @override
    def __str__(self) -> str:
        if self.target and self.value:
            return f"YieldConstraint: {self.target} = yield {self.value}"
        if self.value:
            return f"YieldConstraint: yield {self.value}"
        return "YieldConstraint: yield"


@dataclass(frozen=True)
class AwaitConstraint(Constraint):
    """Await constraint: target = await value.

    Represents awaiting a coroutine/awaitable. The awaited value's result
    (from its $await_result field) flows into the target variable.

    Attributes:
        awaitable: Variable holding the awaitable/coroutine object
        target: Variable to receive the await result (or None)
    """

    awaitable: "Variable"
    target: Optional["Variable"]
    call_site: Optional["CallSite"] = None
    operation: str = "await"
    completion_target: Optional["Variable"] = None
    sent_value: Optional["Variable"] = None
    suspension_target: Optional["Variable"] = None

    def __post_init__(self) -> None:
        if self.operation not in {
            "await",
            "iter",
            "next",
            "advance",
            "aiter",
            "anext",
            "async_resume",
            "await_complete",
            "delegate",
            "delegate_iterator",
        }:
            raise ValueError("unsupported suspension operation")

    @override
    def variables(self) -> set["Variable"]:
        """Get variables involved."""
        vars = {self.awaitable}
        if self.target:
            vars.add(self.target)
        if self.completion_target:
            vars.add(self.completion_target)
        if self.sent_value:
            vars.add(self.sent_value)
        if self.suspension_target:
            vars.add(self.suspension_target)
        return vars

    @override
    def __str__(self) -> str:
        if self.operation != "await":
            return f"SuspensionConstraint[{self.operation}]: {self.awaitable} -> {self.target}"
        if self.target:
            return f"AwaitConstraint: {self.target} = await {self.awaitable}"
        return f"AwaitConstraint: await {self.awaitable}"


type ConstraintTrigger = Variable | Ctx[Variable | FieldAccess]


class ConstraintManager:
    """Efficient constraint storage and indexing.

    Provides fast lookup of constraints by variable and by type.
    """

    def __init__(self) -> None:
        """Initialize empty constraint manager."""
        self._constraints: set[tuple[Scope, Constraint]] = set()
        self._by_variable: dict[ConstraintTrigger, list[tuple[Scope, Constraint]]] = defaultdict(
            list
        )
        self._by_type: dict[type[Constraint], list[tuple[Scope, Constraint]]] = defaultdict(list)

    def add(self, scope: Scope, var: ConstraintTrigger, constraint: Constraint) -> bool:
        """Register one trigger; return whether this subscription is new.

        A scoped constraint can depend on several qualified variables. The
        canonical and type indexes contain it once, independently of the number
        of trigger subscriptions.
        """
        entry = (scope, constraint)
        if entry in self._by_variable.get(var, ()):
            return False
        self._by_variable[var].append(entry)
        if entry not in self._constraints:
            self._constraints.add(entry)
            self._by_type[type(constraint)].append(entry)
        return True

    def remove(self, scope: Scope, var: ConstraintTrigger, constraint: Constraint) -> bool:
        """Remove exactly one trigger subscription, retaining other triggers."""
        entry = (scope, constraint)
        subscriptions = self._by_variable.get(var, [])
        if entry not in subscriptions:
            return False
        subscriptions.remove(entry)
        if not subscriptions:
            self._by_variable.pop(var, None)
        if not any(entry in entries for entries in self._by_variable.values()):
            self._constraints.remove(entry)
            self._by_type[type(constraint)].remove(entry)
            if not self._by_type[type(constraint)]:
                self._by_type.pop(type(constraint))
        return True

    def get_by_variable(self, var: ConstraintTrigger) -> list[Constraint]:
        """Get all constraints involving variable.

        Args:
            var: Variable to query

        Returns:
            Set of constraints (empty if none)
        """
        return [constraint for _, constraint in self._by_variable.get(var, [])]

    def iter_scoped_by_variable(self, var: ConstraintTrigger) -> list[tuple[Scope, Constraint]]:
        """Iterate constraints with their defining scope for a variable."""
        return list(self._by_variable.get(var, []))

    def iter_scoped(self) -> Iterable[tuple[ConstraintTrigger, Scope, Constraint]]:
        """Iterate all constraints with their trigger variables and scopes."""
        for var, scoped_constraints in self._by_variable.items():
            for scope, constraint in scoped_constraints:
                yield var, scope, constraint

    def get_by_type(self, constraint_type: type[Constraint]) -> list[Constraint]:
        """Get all constraints of given type.

        Args:
            constraint_type: Type to query

        Returns:
            Set of constraints of that type
        """
        return [constraint for _, constraint in self._by_type.get(constraint_type, [])]

    def all(self) -> set[tuple[Scope, Constraint]]:
        """Get all constraints.

        Returns:
            Copy of all constraints
        """
        return self._constraints.copy()

    def __len__(self) -> int:
        """Get number of constraints."""
        return len(self._constraints)
