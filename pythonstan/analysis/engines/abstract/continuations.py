"""Source continuation semantics for cleanup, iteration and suspended functions.

Continuation commands contain ordinary abstract values, stores and frames. They
are interpreted by the same Transfer; target Python is never executed by host.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import replace
from typing import TYPE_CHECKING, assert_never

from pythonstan.analysis.contracts.identity import (
    ContextId,
    ContinuationId,
    FrameId,
    ObjectId,
    ProgramPointId,
)
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.semantics.control import resume_pending
from pythonstan.analysis.state.continuation import (
    AssignResume,
    CallResumeContext,
    Continuation,
    ContinuationCommand,
    DelegateResume,
    ExprResume,
    FinallyResume,
    ForResume,
    GraphAttachmentCommand,
    HandlerContext,
    OpaqueResume,
    PendingCompletion,
    ResumeCommand,
    ReturnResume,
    TryPhase,
    TryResume,
    WhileResume,
    WithResume,
)
from pythonstan.analysis.state.effects import MetadataAspect, MetadataUpdate
from pythonstan.analysis.state.flow import (
    ExceptionValue,
    Flow,
    FlowTail,
    NormalExprFlow,
    RaiseFlow,
    SuspendFlow,
    flow_payload,
    make_completion,
    replace_flow,
    require_payload,
)
from pythonstan.analysis.state.handles import ObjectHandle, StateHandle
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.analysis.state.metadata import GeneratorMeta
from pythonstan.analysis.state.suspensions import suspension_states

from .state import Frame, FunctionInfo, Store

if TYPE_CHECKING:
    from .transfer import Transfer


class ContinuationEngine:
    def __init__(self, transfer: Transfer) -> None:
        self.t: Transfer = transfer
        self.d = transfer.d
        self.ops = transfer.ops
        self.active: list[tuple[ObjectHandle, FrameId]] = []
        self.exception_contexts: list[CallResumeContext] = []

    def normal(self, store: Store, value: Value | None = None) -> NormalExprFlow:
        return self.t.normal(store, value)

    def wrap(self, flow: Flow, command: ResumeCommand) -> Flow:
        point = flow.continuation.source_point
        match command:
            case (
                TryResume(node, _, _, _)
                | FinallyResume(node, _, _, _)
                | ForResume(node, _, _)
                | WhileResume(node, _)
                | WithResume(node, _, _)
                | DelegateResume(node, _, _)
            ):
                point = self.d.point(node)
            case AssignResume(targets, _):
                if targets:
                    point = self.d.point(targets[0])
            case ReturnResume(inner) | ExprResume(inner):
                point = inner.source_point
            case OpaqueResume() | GraphAttachmentCommand():
                pass
            case _:
                assert_never(command)
        identity = ContinuationId(f"{self.d.current}:{point}:{type(command).__name__}")
        return replace_flow(
            flow,
            continuation=Continuation((command,), identity, point),
            cleanups=Continuation(),
            resume_target=None,
        )

    def block(
        self, body: Iterable[ContinuationCommand], flows: list[Flow], frame: Frame
    ) -> list[Flow]:
        commands = tuple(body)
        self.t.setup_annotations(commands, flows, frame)
        for position, node in enumerate(commands):
            result: list[Flow] = []
            for flow in flows:
                if not isinstance(node, ast.stmt):
                    produced = self.marker(node, flow, frame)
                elif flow.kind == "normal":
                    produced = (
                        self.d.region_scheduler.execute(node, flow, frame, self.d)
                        if self.d.region_scheduler is not None
                        else self.t.statement(node, flow.store, frame)
                    )
                else:
                    produced = [flow]
                for out in produced:
                    if out.kind == "suspend":
                        out = replace_flow(
                            out,
                            continuation=Continuation(
                                (*out.continuation.commands, *commands[position + 1 :]),
                                out.continuation.identity,
                                out.continuation.source_point,
                            ),
                        )
                    result.append(out)
            execution = (
                self.d.region_scheduler.execution_for(node, frame, self.d)
                if isinstance(node, ast.stmt) and self.d.region_scheduler is not None
                else self.t.execution
            )
            flows = self.d.compact(result, execution=execution)
            if not flows:
                break
        return flows

    def marker(self, command: ResumeCommand, incoming: Flow, frame: Frame) -> list[Flow]:
        result: list[Flow]
        match command:
            case AssignResume(targets, inner):
                result = []
                for flow in self.block(inner, [incoming], frame):
                    if flow.kind == "suspend":
                        result.append(self.wrap(flow, AssignResume(targets, flow.continuation)))
                        continue
                    pending: list[Flow] = [flow]
                    for target in targets:
                        pending = [
                            out
                            for candidate in pending
                            for out in (
                                self.t.assign(
                                    target, require_payload(flow), candidate.store, frame, target
                                )
                                if candidate.kind == "normal"
                                else [candidate]
                            )
                        ]
                    result.extend(pending)
                return result
            case ReturnResume(inner) | ExprResume(inner):
                result = []
                for flow in self.block(inner, [incoming], frame):
                    if flow.kind == "suspend":
                        wrapped = (
                            ReturnResume(flow.continuation)
                            if isinstance(command, ReturnResume)
                            else ExprResume(flow.continuation)
                        )
                        result.append(self.wrap(flow, wrapped))
                    elif flow.kind == "normal":
                        result.append(
                            replace_flow(flow, kind="return")
                            if isinstance(command, ReturnResume)
                            else self.normal(flow.store)
                        )
                    else:
                        result.append(flow)
                return result
            case OpaqueResume(reason):
                return (
                    self.t.unknown(incoming.store, None, reason)
                    if incoming.kind == "normal"
                    else [incoming]
                )
            case TryResume(node, phase, handler, inner):
                return self.finish_try(
                    node, self.block(inner, [incoming], frame), frame, phase, handler
                )
            case FinallyResume(node, held, previous, inner):
                result = []
                pending_exit = make_completion(
                    incoming.state, held.kind, held.value, held.exception, FlowTail()
                )
                for flow in self.block(inner, [incoming], frame):
                    if flow.kind == "suspend":
                        result.append(
                            self.wrap(
                                flow,
                                FinallyResume(
                                    node,
                                    PendingCompletion(
                                        pending_exit.kind,
                                        flow_payload(pending_exit),
                                        pending_exit.exception,
                                    ),
                                    previous,
                                    flow.continuation,
                                ),
                            )
                        )
                        continue
                    if previous is not None:
                        self.t._restore_exception(flow.store, previous)
                    result.append(resume_pending(pending_exit, flow))
                return result
            case ForResume(node, iterator, inner):
                return self.finish_for_body(
                    node, iterator, self.block(inner, [incoming], frame), frame
                )
            case WhileResume(node, inner):
                result = []
                for flow in self.block(inner, [incoming], frame):
                    if flow.kind == "suspend":
                        result.append(self.wrap(flow, WhileResume(node, flow.continuation)))
                    elif flow.kind in {"normal", "continue"}:
                        result.extend(self.t.while_loop(node, flow.store, frame))
                    elif flow.kind == "break":
                        result.append(self.normal(flow.store))
                    else:
                        result.append(flow)
                return result
            case WithResume(node, exits, inner):
                return self.cleanup_with(node, exits, self.block(inner, [incoming], frame), frame)
            case DelegateResume(node, iterator, _):
                return self.delegate_advance(node, iterator, incoming, frame)
            case GraphAttachmentCommand():
                return self.t.unknown(incoming.store, None, "unknown_continuation_command")
            case _:
                assert_never(command)

    def try_statement(self, node: ast.Try | ast.TryStar, store: Store, frame: Frame) -> list[Flow]:
        return self.finish_try(
            node, self.block(node.body, [self.normal(store.copy())], frame), frame, "body", None
        )

    def finish_try(
        self,
        node: ast.Try | ast.TryStar,
        flows: list[Flow],
        frame: Frame,
        phase: TryPhase,
        extra: HandlerContext | None,
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        for flow in flows:
            if flow.kind == "suspend":
                result.append(self.wrap(flow, TryResume(node, phase, extra, flow.continuation)))
                continue
            if phase == "handler":
                if extra is None:
                    raise ValueError("Handler continuation requires its exception context")
                name, previous = extra.name, extra.previous
                if name:
                    self.t.write_name(name, Value(unbound=True), flow.store, frame, node)
                self.t._restore_exception(flow.store, previous)
                result.append(flow)
                continue
            if phase == "orelse":
                result.append(flow)
                continue
            if flow.kind == "normal":
                for out in self.block(node.orelse, [flow], frame):
                    result.append(
                        self.wrap(out, TryResume(node, "orelse", None, out.continuation))
                        if out.kind == "suspend"
                        else out
                    )
                continue
            if flow.kind != "raise":
                result.append(flow)
                continue
            remaining: list[Flow] = [flow]
            for handler in node.handlers:
                next_remaining: list[Flow] = []
                for raised in remaining:
                    evaluations = (
                        [NormalExprFlow(StateHandle.capture(raised.store.copy()), Value.bottom())]
                        if handler.type is None
                        else self.t.expr(handler.type, raised.store.copy(), frame)
                    )
                    for evaluated in evaluations:
                        if evaluated.kind != "normal":
                            result.append(evaluated)
                            continue
                        tags, uncertain, invalid = (
                            ({"builtin:BaseException"}, False, False)
                            if handler.type is None
                            else self.t.handler_tags(require_payload(evaluated), evaluated.store)
                        )
                        raised_types = self.t.raised_tags(raised)
                        matches = (
                            bool(tags & raised_types) if raised_types is not None else bool(tags)
                        )
                        may_escape = (
                            not matches
                            or uncertain
                            or raised_types is None
                            and "builtin:BaseException" not in tags
                        )
                        if uncertain:
                            self.d.open("unknown_exception_handler", evaluated.store, handler)
                        if invalid:
                            result.append(self.t.error(evaluated.store.copy(), "TypeError"))
                        if matches or uncertain:
                            current = evaluated.store.copy()
                            previous = self.t._active_exception(current, raised, frame.identity)
                            if handler.name:
                                self.t.write_name(
                                    handler.name, require_payload(raised), current, frame, handler
                                )
                            for handled in self.block(handler.body, [self.normal(current)], frame):
                                if handled.kind == "suspend":
                                    result.append(
                                        self.wrap(
                                            handled,
                                            TryResume(
                                                node,
                                                "handler",
                                                HandlerContext(handler.name, previous),
                                                handled.continuation,
                                            ),
                                        )
                                    )
                                else:
                                    if handler.name:
                                        self.t.write_name(
                                            handler.name,
                                            Value(unbound=True),
                                            handled.store,
                                            frame,
                                            handler,
                                        )
                                    self.t._restore_exception(handled.store, previous)
                                    result.append(handled)
                        if may_escape and (tags or uncertain):
                            next_remaining.append(replace_flow(raised, store=evaluated.store))
                remaining = next_remaining
            result.extend(remaining)
        if node.finalbody:
            cleaned: list[Flow] = []
            for pending in result:
                if pending.kind == "suspend":
                    cleaned.append(pending)
                    continue
                current = pending.store.copy()
                finally_previous = (
                    self.t._active_exception(current, pending, frame.identity)
                    if pending.kind == "raise"
                    else None
                )
                for out in self.block(node.finalbody, [self.normal(current)], frame):
                    if out.kind == "suspend":
                        cleaned.append(
                            self.wrap(
                                out,
                                FinallyResume(
                                    node,
                                    PendingCompletion(
                                        pending.kind, flow_payload(pending), pending.exception
                                    ),
                                    finally_previous,
                                    out.continuation,
                                ),
                            )
                        )
                        continue
                    if finally_previous is not None:
                        self.t._restore_exception(out.store, finally_previous)
                    cleaned.append(resume_pending(pending, out))
            result = cleaned
        return result

    def special(
        self,
        value: Value,
        name: str,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        missing_error: str = "AttributeError",
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj and obj.kind == "instance":
                hook = Value.bottom()
                missing = not obj.class_ids
                for cls in obj.class_ids:
                    alternative, absent = self.t.attributes.lookup_presence(cls, name, store)
                    hook = self.ops.join(hook, alternative)
                    missing |= absent
                if not hook.is_bottom():
                    result.extend(
                        self.t.attributes._descriptor_get(
                            hook,
                            Value.ref(ref),
                            Value(refs=obj.class_ids),
                            store.copy(),
                            frame,
                            node,
                        )
                    )
                if missing:
                    result.append(self.t.error(store.copy(), missing_error))
            else:
                for out in self.t.getattr(Value.ref(ref), name, store.copy(), frame, node):
                    result.append(
                        self.t.error(out.store, missing_error)
                        if out.kind == "raise" and out.exception == "AttributeError"
                        else out
                    )
        scalar = replace(value, refs=frozenset())
        if not scalar.is_bottom():
            for out in self.t.getattr(scalar, name, store.copy(), frame, node):
                result.append(
                    self.t.error(out.store, missing_error)
                    if out.kind == "raise" and out.exception == "AttributeError"
                    else out
                )
        return result

    def get_iterator(
        self,
        value: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        asynchronous: bool = False,
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        if asynchronous:
            for method in self.special(value, "__aiter__", store, frame, node, "TypeError"):
                if method.kind != "normal":
                    result.append(method)
                    continue
                result.extend(
                    self.t.invoke(require_payload(method), [], (), method.store, frame, node)
                )
            return result
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj and obj.kind in {"iterator", "generator"}:
                result.append(NormalExprFlow(StateHandle.capture(store.copy()), Value.ref(ref)))
                continue
            if obj and obj.kind == "dict_view":
                result.extend(
                    self.t.unknown(store, node, "havoc_dictionary_view_iteration")
                    if obj.havoc
                    else self.t.dictionary_views.iterator(
                        obj.metadata.view_owner(),
                        obj.metadata["view_kind"],
                        store.copy(),
                        frame,
                        node,
                    )
                )
                continue
            if obj and obj.kind == "dict":
                result.extend(
                    self.t.dictionary_views.iterator(
                        Value.ref(ref), "keys", store.copy(), frame, node
                    )
                )
                continue
            if obj and obj.kind in {"list", "tuple", "set", "range"}:
                s = store.copy()
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(s),
                        self.t.alloc(
                            node,
                            s,
                            frame,
                            "iterator",
                            metadata={"container": Value.ref(ref), "index": 0},
                        ),
                    )
                )
                continue
            if obj and obj.kind == "instance":
                hooks = self.ops.join_all(
                    self.t.lookup_class(c, "__iter__", store) for c in obj.class_ids
                )
                if not hooks.is_bottom():
                    s = store.copy()
                    bound = self.t.bound(hooks, Value.ref(ref), s, frame, node)
                    for out in self.t.invoke(bound, [], (), s, frame, node):
                        if out.kind != "normal":
                            result.append(out)
                            continue
                        valid = False
                        unknown = require_payload(out).top or bool(require_payload(out).top_sorts)
                        for target in require_payload(out).refs:
                            item = out.store.heap.get(target)
                            if (
                                item
                                and item.kind in {"iterator", "generator"}
                                or (
                                    item
                                    and item.kind == "instance"
                                    and not self.ops.join_all(
                                        self.t.lookup_class(c, "__next__", out.store)
                                        for c in item.class_ids
                                    ).is_bottom()
                                )
                            ):
                                valid = True
                            else:
                                result.append(self.t.error(out.store.copy(), "TypeError"))
                        if valid:
                            result.append(out)
                        if unknown:
                            result.extend(
                                self.t.unknown(out.store, node, "unknown_iterator_result")
                            )
                    continue
                getitem = self.ops.join_all(
                    self.t.lookup_class(c, "__getitem__", store) for c in obj.class_ids
                )
                if not getitem.is_bottom():
                    s = store.copy()
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(s),
                            self.t.alloc(
                                node,
                                s,
                                frame,
                                "iterator",
                                metadata={
                                    "container": Value.ref(ref),
                                    "index": 0,
                                    "sequence_protocol": True,
                                },
                            ),
                        )
                    )
                    continue
            result.append(self.t.error(store.copy(), "TypeError"))
        strings = value.strings.get_consts()
        if strings is not None:
            for text in strings:
                s = store.copy()
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(s),
                        self.t.alloc(
                            node,
                            s,
                            frame,
                            "iterator",
                            metadata={
                                "sequence": tuple(Value.constant(c) for c in text),
                                "index": 0,
                            },
                        ),
                    )
                )
        elif not value.strings.is_bottom():
            result.extend(self.t.unknown(store, node, "abstract_string_iteration"))
        if value.top or value.top_sorts:
            result.extend(self.t.unknown(store, node, "unknown_iteration_protocol"))
        if not value.ints.is_bottom or value.bools or value.none or value.floats:
            result.append(self.t.error(store.copy(), "TypeError"))
        return result

    def next_iterator(
        self,
        iterator: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        asynchronous: bool = False,
    ) -> list[Flow]:
        result: list[Flow]
        if not asynchronous:
            return self.resume(iterator, store, frame, node, "next")
        result = []
        for method in self.special(iterator, "__anext__", store, frame, node, "TypeError"):
            if method.kind != "normal":
                result.append(method)
                continue
            for called in self.t.invoke(require_payload(method), [], (), method.store, frame, node):
                if called.kind != "normal":
                    result.append(called)
                else:
                    result.extend(
                        self.resume(require_payload(called), called.store, frame, node, "await")
                    )
        return result

    def is_stop(self, flow: Flow, asynchronous: bool = False) -> bool:
        if flow.kind != "raise":
            return False
        name = "StopAsyncIteration" if asynchronous else "StopIteration"
        tags = self.t.raised_tags(flow)
        return flow.exception == name or tags is not None and "builtin:" + name in tags

    def collect(
        self, value: Value, store: Store, frame: Frame, node: ast.AST | None, kind: str
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        states: list[tuple[Store, Value, tuple[Value, ...]]] = []
        for iterator_flow in self.get_iterator(value, store, frame, node):
            if iterator_flow.kind == "normal":
                states.append((iterator_flow.store, require_payload(iterator_flow), ()))
            else:
                result.append(iterator_flow)
        raw_limit = self.t.execution.integer("max_stmt_visits", 100)
        if not isinstance(raw_limit, (str, int, float)):
            raise TypeError("max_stmt_visits must be numeric")
        limit = max(1, int(raw_limit))
        for _ in range(limit):
            following: list[tuple[Store, Value, tuple[Value, ...]]] = []
            for current, iterator, values in states:
                for advanced in self.next_iterator(iterator, current, frame, node):
                    if self.is_stop(advanced):
                        fields: dict[FieldKey, Value] = dict(enumerate(values))
                        if kind == "set":
                            known: dict[FieldKey, Value] = {}
                            uncertain = False
                            for value_ in values:
                                constants = value_.exact_constants()
                                if constants is None or len(constants) != 1:
                                    uncertain = True
                                    break
                                known.setdefault(constants[0], value_)
                            if uncertain:
                                result.extend(
                                    self.t.unknown(
                                        advanced.store, node, "set_constructor_key_protocol"
                                    )
                                )
                                continue
                            fields = dict(enumerate(known.values()))
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(advanced.store),
                                self.t.alloc(
                                    node,
                                    advanced.store,
                                    frame,
                                    kind,
                                    fields,
                                    {"length": len(fields)},
                                ),
                            )
                        )
                    elif advanced.kind == "normal":
                        following.append(
                            (advanced.store, iterator, (*values, require_payload(advanced)))
                        )
                    else:
                        result.append(advanced)
            states = following
            if not states:
                return self.d.compact(result, execution=self.t.execution)
            if len(states) > self.t.execution.max_paths:
                break
        for current, _, _ in states:
            result.extend(self.t.unknown(current, node, "iterator_collection_budget"))
        return self.d.compact(result, execution=self.t.execution)

    def for_loop(self, node: ast.For | ast.AsyncFor, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        result = []
        for evaluated in self.t.expr(node.iter, store, frame):
            if evaluated.kind != "normal":
                result.append(evaluated)
                continue
            for iterator in self.get_iterator(
                require_payload(evaluated),
                evaluated.store,
                frame,
                node,
                isinstance(node, ast.AsyncFor),
            ):
                if iterator.kind != "normal":
                    result.append(iterator)
                    continue
                result.extend(
                    self.advance_for(
                        node, require_payload(iterator), [self.normal(iterator.store)], frame
                    )
                )
        return self.d.compact(result, execution=self.t.execution)

    def finish_for_body(
        self, node: ast.For | ast.AsyncFor, iterator: Value, flows: list[Flow], frame: Frame
    ) -> list[Flow]:
        result: list[Flow]
        again: list[Flow] = []
        result = []
        for out in flows:
            if out.kind == "suspend":
                result.append(self.wrap(out, ForResume(node, iterator, out.continuation)))
            elif out.kind in {"normal", "continue"}:
                again.append(self.normal(out.store))
            elif out.kind == "break":
                result.append(self.normal(out.store))
            else:
                result.append(out)
        if again:
            result.extend(self.advance_for(node, iterator, again, frame))
        return result

    def advance_for(
        self, node: ast.For | ast.AsyncFor, iterator: Value, starting: list[Flow], frame: Frame
    ) -> list[Flow]:
        result: list[Flow]
        states = starting
        result = []
        raw_limit = self.t.execution.integer("max_stmt_visits", 100)
        if not isinstance(raw_limit, (str, int, float)):
            raise TypeError("max_stmt_visits must be numeric")
        limit = max(1, int(raw_limit))
        for _ in range(limit):
            next_states: list[Flow] = []
            for state in states:
                for advanced in self.next_iterator(
                    iterator, state.store, frame, node, isinstance(node, ast.AsyncFor)
                ):
                    if self.is_stop(advanced, isinstance(node, ast.AsyncFor)):
                        result.extend(self.block(node.orelse, [self.normal(advanced.store)], frame))
                        continue
                    if advanced.kind != "normal":
                        result.append(advanced)
                        continue
                    for assigned in self.t.assign(
                        node.target, require_payload(advanced), advanced.store, frame, node
                    ):
                        if assigned.kind != "normal":
                            result.append(assigned)
                            continue
                        for body in self.block(node.body, [assigned], frame):
                            if body.kind == "suspend":
                                result.append(
                                    self.wrap(body, ForResume(node, iterator, body.continuation))
                                )
                            elif body.kind in {"normal", "continue"}:
                                next_states.append(self.normal(body.store))
                            elif body.kind == "break":
                                result.append(self.normal(body.store))
                            else:
                                result.append(body)
            states = self.d.compact(next_states, execution=self.t.execution)
            if not states:
                return self.d.compact(result, execution=self.t.execution)
        for state in states:
            self.d.open("for_loop_budget", state.store, node)
            for opaque in self.t.unknown(
                self.t.havoc_loop_names(node, state.store, frame), node, "for_loop_budget"
            ):
                result.append(opaque)
        return self.d.compact(result, execution=self.t.execution)

    def with_statement(
        self, node: ast.With | ast.AsyncWith, store: Store, frame: Frame
    ) -> list[Flow]:
        result: list[Flow]
        paths: list[tuple[Store, tuple[Value, ...]]] = [(store.copy(), ())]
        result = []
        async_ = isinstance(node, ast.AsyncWith)
        enter_name = "__aenter__" if async_ else "__enter__"
        exit_name = "__aexit__" if async_ else "__exit__"
        for item in node.items:
            following = []
            for current, exits in paths:
                for manager in self.t.expr(item.context_expr, current, frame):
                    if manager.kind != "normal":
                        result.extend(self.cleanup_with(node, exits, [manager], frame))
                        continue
                    for exit_method in self.special(
                        require_payload(manager), exit_name, manager.store, frame, node, "TypeError"
                    ):
                        if exit_method.kind != "normal":
                            result.extend(self.cleanup_with(node, exits, [exit_method], frame))
                            continue
                        for enter in self.special(
                            require_payload(manager),
                            enter_name,
                            exit_method.store,
                            frame,
                            node,
                            "TypeError",
                        ):
                            if enter.kind != "normal":
                                result.extend(self.cleanup_with(node, exits, [enter], frame))
                                continue
                            for entered in self.t.invoke(
                                require_payload(enter), [], (), enter.store, frame, node
                            ):
                                entered_flows = (
                                    self.resume(
                                        require_payload(entered),
                                        entered.store,
                                        frame,
                                        node,
                                        "await",
                                    )
                                    if async_ and entered.kind == "normal"
                                    else [entered]
                                )
                                for completed in entered_flows:
                                    if completed.kind != "normal":
                                        result.extend(
                                            self.cleanup_with(node, exits, [completed], frame)
                                        )
                                        continue
                                    acquired = (*exits, require_payload(exit_method))
                                    assigned = (
                                        self.t.assign(
                                            item.optional_vars,
                                            require_payload(completed),
                                            completed.store,
                                            frame,
                                            node,
                                        )
                                        if item.optional_vars
                                        else [self.normal(completed.store)]
                                    )
                                    for flow in assigned:
                                        if flow.kind == "normal":
                                            following.append((flow.store, acquired))
                                        else:
                                            result.extend(
                                                self.cleanup_with(node, acquired, [flow], frame)
                                            )
            paths = following
        for current, exits in paths:
            result.extend(
                self.cleanup_with(
                    node, exits, self.block(node.body, [self.normal(current)], frame), frame
                )
            )
        return self.d.compact(result, execution=self.t.execution)

    def cleanup_with(
        self,
        node: ast.With | ast.AsyncWith,
        exits: tuple[Value, ...],
        flows: list[Flow],
        frame: Frame,
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        pending: list[Flow] = []
        for flow in flows:
            if flow.kind == "suspend":
                result.append(self.wrap(flow, WithResume(node, exits, flow.continuation)))
            else:
                pending.append(flow)
        for method in reversed(exits):
            cleaned: list[Flow] = []
            for flow in pending:
                if flow.kind == "raise":
                    tags: set[ObjectId] = set()
                    for ref in require_payload(flow).refs:
                        obj = flow.store.heap.get(ref)
                        if obj:
                            tags.update(obj.class_ids)
                    if not tags and flow.exception and flow.exception != "AnyException":
                        tags.add(ObjectId("builtin:" + flow.exception))
                    exception_type = Value(refs=frozenset(tags)) if tags else Value.unknown()
                    parameters = [exception_type, require_payload(flow), Value.constant(None)]
                else:
                    parameters = [Value.constant(None)] * 3
                for called in self.t.invoke(method, parameters, (), flow.store, frame, node):
                    completed = (
                        self.resume(require_payload(called), called.store, frame, node, "await")
                        if isinstance(node, ast.AsyncWith) and called.kind == "normal"
                        else [called]
                    )
                    for cleanup in completed:
                        if cleanup.kind != "normal":
                            cleaned.append(cleanup)
                        elif flow.kind == "raise":
                            for tested in self.t.truth_flows(
                                require_payload(cleanup), cleanup.store, frame, node
                            ):
                                if tested.kind != "normal":
                                    cleaned.append(tested)
                                else:
                                    if True in require_payload(tested).bools:
                                        cleaned.append(self.normal(tested.store.copy()))
                                    if False in require_payload(tested).bools:
                                        cleaned.append(replace_flow(flow, store=tested.store))
                        else:
                            cleaned.append(replace_flow(flow, store=cleanup.store))
            pending = cleaned
        return result + pending

    def stop(
        self,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        value: Value | None = None,
        async_: bool = False,
    ) -> RaiseFlow:
        value = Value.constant(None) if value is None else value
        name = "StopAsyncIteration" if async_ else "StopIteration"
        for exception in self.t.builtin(name, [value], (), store, frame, node):
            if exception.kind == "normal":
                for ref in require_payload(exception).refs:
                    exception.store.heap[ref].fields["value"] = value
                self.d.exceptions.add(name)
                return RaiseFlow(
                    StateHandle.capture(exception.store),
                    ExceptionValue(require_payload(exception), name),
                )
        return self.t.error(store, name)

    def stop_value(self, flow: Flow) -> Value:
        values = []
        for ref in require_payload(flow).refs:
            obj = flow.store.heap.get(ref)
            if obj:
                if "value" in obj.fields:
                    values.append(obj.fields["value"])
                else:
                    for args in obj.fields.get("args", Value.bottom()).refs:
                        item = flow.store.heap.get(args)
                        if item:
                            values.append(item.fields.get(0, Value.constant(None)))
        return self.ops.join_all(values) if values else Value.constant(None)

    def delegate_start(self, node: ast.YieldFrom, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        result = []
        for source in self.t.expr(node.value, store, frame):
            if source.kind != "normal":
                result.append(source)
                continue
            for iterator in self.get_iterator(require_payload(source), source.store, frame, node):
                if iterator.kind != "normal":
                    result.append(iterator)
                else:
                    result.extend(
                        self.delegate_advance(
                            node, require_payload(iterator), self.normal(iterator.store), frame
                        )
                    )
        return result

    def delegate_advance(
        self, node: ast.YieldFrom, iterator: Value, incoming: Flow, frame: Frame
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        advanced: list[Flow] = []
        if incoming.kind == "raise":
            closing = incoming.exception == "GeneratorExit"
            for method in self.t.getattr(
                iterator, "close" if closing else "throw", incoming.store.copy(), frame, node
            ):
                if method.kind == "raise" and method.exception == "AttributeError":
                    advanced.append(incoming)
                    continue
                if method.kind != "normal":
                    advanced.append(method)
                    continue
                for called in self.t.invoke(
                    require_payload(method),
                    [] if closing else [require_payload(incoming)],
                    (),
                    method.store,
                    frame,
                    node,
                ):
                    advanced.append(
                        replace_flow(incoming, store=called.store)
                        if closing and called.kind == "normal"
                        else called
                    )
        elif require_payload(incoming).exact_constants() == [None]:
            advanced = self.next_iterator(iterator, incoming.store, frame, node)
        else:
            for method in self.t.getattr(iterator, "send", incoming.store, frame, node):
                if method.kind != "normal":
                    advanced.append(method)
                else:
                    advanced.extend(
                        self.t.invoke(
                            require_payload(method),
                            [require_payload(incoming)],
                            (),
                            method.store,
                            frame,
                            node,
                        )
                    )
        for flow in advanced:
            if self.is_stop(flow):
                result.append(
                    NormalExprFlow(StateHandle.capture(flow.store), self.stop_value(flow))
                )
            elif flow.kind == "raise" and flow.exception == "AnyException":
                result.append(
                    NormalExprFlow(StateHandle.capture(flow.store.copy()), Value.unknown())
                )
                result.append(flow)
            elif flow.kind == "normal":
                result.append(
                    SuspendFlow(
                        StateHandle.capture(flow.store),
                        require_payload(flow),
                        FlowTail(
                            continuation=Continuation(
                                (DelegateResume(node, iterator, Continuation()),),
                                ContinuationId(f"{self.d.current}:{self.d.point(node)}:delegate"),
                                self.d.point(node),
                            )
                        ),
                    )
                )
            else:
                result.append(flow)
        return result

    def resume(
        self,
        receiver: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        operation: str,
        value: Value | None = None,
    ) -> list[Flow]:
        """Resume one correlated state at a time, retaining the residual graph.

        A summarized owner may denote several lazy frames. Selecting a frame
        does not establish singleton ownership: each result weakly retains
        unselected and pre-resume states in tracked heap metadata, so an epoch
        publication transforms every alternative together with its roots.
        """
        store.ensure_current(self.ops)
        receiver = self.ops.normalize(receiver)
        result: list[Flow] = []
        for ref in receiver.refs:
            obj = store.heap.get(ref)
            record = None if obj is None else obj.metadata.record
            if obj is not None and isinstance(record, GeneratorMeta) and not obj.havoc:
                count = len(suspension_states(record))
                for index in range(count):
                    current = store.copy()
                    item = current.heap[ref]
                    states_record = item.metadata.record
                    assert isinstance(states_record, GeneratorMeta)
                    states = suspension_states(states_record)
                    selected = states[index]
                    residual = states[:index] + states[index + 1 :]
                    if not item.singleton:
                        residual += (selected,)
                    item.metadata.replace_record(replace(selected, alternatives=residual))
                    result.extend(
                        self._resume_primary(Value.ref(ref), current, frame, node, operation, value)
                    )
            else:
                result.extend(
                    self._resume_primary(Value.ref(ref), store, frame, node, operation, value)
                )
        if receiver.top or receiver.top_sorts:
            result.extend(
                self._resume_primary(
                    replace(receiver, refs=frozenset()), store, frame, node, operation, value
                )
            )
        return self.d.compact(result, execution=self.t.execution)

    def _resume_primary(
        self,
        receiver: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        operation: str,
        value: Value | None = None,
    ) -> list[Flow]:
        result: list[Flow]
        value = Value.constant(None) if value is None else value
        result = []
        if hasattr(store, "ensure_current"):
            store.ensure_current(self.ops)
        if hasattr(self.ops, "normalize"):
            receiver = self.ops.normalize(receiver)
        for ref in receiver.refs:
            obj = store.heap.get(ref)
            if obj is None:
                result.extend(self.t.unknown(store, node, "unknown_resume"))
                continue
            current = store.copy()
            item = current.heap[ref]
            if obj.havoc or obj.kind == "iterator" and not obj.singleton:
                reason = (
                    "suspension_state_capacity_exceeded"
                    if isinstance(obj.metadata.record, GeneratorMeta)
                    and obj.metadata.record.overflow
                    else "havoc_suspended_or_iterator_state"
                )
                result.extend(self.t.unknown(current, node, reason))
                continue
            if obj.kind == "instance":
                for method in self.special(Value.ref(ref), "__next__", current, frame, node):
                    if method.kind != "normal":
                        result.append(method)
                    else:
                        result.extend(
                            self.t.invoke(
                                require_payload(method), [], (), method.store, frame, node
                            )
                        )
                continue
            if obj.kind == "iterator":
                if operation not in {"next", "send"}:
                    result.append(self.t.error(current, "AttributeError"))
                    continue
                if "dictionary_iteration" in item.metadata:
                    result.extend(self.t.dictionary_views.next(ref, current, frame, node))
                    continue
                index = item.metadata.lookup("index", 0)
                values = []
                missing = False
                if "sequence" in item.metadata:
                    sequence = item.metadata["sequence"]
                    if index < len(sequence):
                        values.append(sequence[index])
                    else:
                        missing = True
                for container in item.metadata.lookup("container", Value.bottom()).refs:
                    source = current.heap.get(container)
                    if item.metadata.lookup("sequence_protocol"):
                        for out in self.t.getitem(
                            Value.ref(container), Value.constant(index), current, frame, node
                        ):
                            if out.kind == "normal":
                                values.append(require_payload(out))
                                current = out.store
                                item = current.heap[ref]
                            elif out.exception == "IndexError":
                                missing = True
                            else:
                                result.append(out)
                    elif source and source.kind in {"list", "tuple", "set", "range"}:
                        got, absent = current.read_slot(container, index, self.ops)
                        if not got.is_bottom():
                            values.append(got)
                        missing |= absent
                    elif source and source.kind == "dict":
                        if source.metadata.lookup("summarized_keys"):
                            result.extend(
                                self.t.unknown(
                                    current,
                                    node,
                                    "summarized_dictionary_key_iteration",
                                    havoc=False,
                                )
                            )
                            missing = True
                            continue
                        keys = list(source.fields)
                        if index < len(keys):
                            values.append(Value.constant(keys[index]))
                        else:
                            missing = True
                    else:
                        result.extend(self.t.unknown(current, node, "dynamic_iterator"))
                        missing = True
                if values:
                    item.metadata["index"] = index + 1
                    current.record_write(MetadataUpdate(ref, MetadataAspect.ITERATOR_STATE))
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(current.copy()), self.ops.join_all(values)
                        )
                    )
                if missing or not values:
                    result.append(self.stop(current, frame, node))
                continue
            if obj.kind == "async_generator":
                result.extend(self.t.unknown(current, node, "async_generator_runtime_protocol"))
                continue
            if obj.kind not in {"generator", "coroutine"}:
                result.append(self.t.error(current, "TypeError"))
                continue
            if operation == "await" and obj.kind != "coroutine":
                result.append(self.t.error(current, "TypeError"))
                continue
            if obj.kind == "coroutine" and operation == "next":
                result.append(self.t.error(current, "TypeError"))
                continue
            if (
                any(
                    handle.refresh(self.ops.arena).identity == ref
                    and activation == item.metadata["frame"].identity
                    for handle, activation in self.active
                )
                or len(self.active) >= 12
            ):
                reason = "recursive_generator_delegation"
                opaque = self.t.unknown(current, node, reason)
                result.extend(opaque)
                result.extend(
                    SuspendFlow(
                        StateHandle.capture(out.store.copy()),
                        Value.unknown(),
                        FlowTail(
                            continuation=Continuation(
                                (OpaqueResume(reason),),
                                ContinuationId(f"{self.d.current}:{self.d.point(node)}:opaque"),
                                self.d.point(node),
                            )
                        ),
                    )
                    for out in opaque
                    if out.kind == "normal"
                )
                continue
            stage = item.metadata.lookup("stage", "created")
            if stage == "closed":
                if operation == "close":
                    result.append(self.normal(current))
                elif operation == "await" or obj.kind == "coroutine":
                    result.append(self.t.error(current, "RuntimeError"))
                elif operation == "throw":
                    result.extend(self.t.raise_value(value, current, frame, node))
                else:
                    result.append(self.stop(current, frame, node))
                continue
            if stage == "created" and operation == "send" and value.exact_constants() != [None]:
                result.append(self.t.error(current, "TypeError"))
                continue
            if stage == "created" and operation in {"close", "throw"}:
                item.metadata["stage"] = "closed"
                current.record_write(MetadataUpdate(ref, MetadataAspect.GENERATOR_STATE))
                result.extend(
                    [self.normal(current)]
                    if operation == "close"
                    else self.t.raise_value(value, current, frame, node)
                )
                continue
            generator_frame = item.metadata["frame"]
            depth = max(0, self.t.execution.context_depth)
            resume_point = (
                self.d.point(node)
                + "#resume:"
                + str(min(32, int(item.metadata.lookup("resume_count", 0))))
            )
            prior_context: tuple[ProgramPointId, ...] = generator_frame.context
            resumed_context = prior_context + (ProgramPointId(resume_point),)
            generator_frame = replace(
                generator_frame, context=ContextId(resumed_context[-depth:] if depth else ())
            )
            caller_exception = self.t.exception_context(current)
            owned_exception = item.metadata.lookup("owned_exception")
            if stage != "created" and owned_exception is not None:
                self.t.install_exception_context(current, owned_exception)
            for cell, initial in item.metadata.entry_handle().read().cells.items():
                if (
                    cell.startswith("local:" + generator_frame.identity + ":")
                    and cell not in current.cells
                ):
                    current.cells[cell] = initial
            if operation == "close":
                injected = self.t.builtin("GeneratorExit", [], (), current, generator_frame, node)
                starting: list[Flow] = [
                    RaiseFlow(
                        StateHandle.capture(out.store),
                        ExceptionValue(require_payload(out), "GeneratorExit"),
                    )
                    for out in injected
                    if out.kind == "normal"
                ]
            elif operation == "throw":
                starting = self.t.raise_value(value, current, generator_frame, node)
            else:
                starting = [NormalExprFlow(StateHandle.capture(current), value)]
            body = item.metadata.lookup("remaining", item.metadata["body"])
            active_object = ObjectHandle(ref, self.ops.arena.epoch)
            self.active.append((active_object, generator_frame.identity))
            self.exception_contexts.append(
                CallResumeContext(caller_exception, generator_frame.identity)
            )
            try:
                flows = self.block(body, starting, generator_frame)
            finally:
                self.active.pop()
                self.exception_contexts.pop()
            for flow in flows:
                flow.store.ensure_current(self.ops)
                resumed_ref = active_object.refresh(self.ops.arena).identity
                target = flow.store.heap[resumed_ref]
                target_record = target.metadata.record
                if isinstance(target_record, GeneratorMeta):
                    states = suspension_states(target_record)
                    selected = next(
                        (
                            state
                            for state in states
                            if state.frame is not None
                            and state.frame.identity == generator_frame.identity
                            and state.stage == stage
                        ),
                        None,
                    )
                    if selected is None:
                        result.extend(
                            self.t.unknown(flow.store, node, "missing_correlated_resume_state")
                        )
                        continue
                    residual = (
                        states
                        if not target.singleton
                        else tuple(state for state in states if state is not selected)
                    )
                    target.metadata.replace_record(replace(selected, alternatives=residual))
                target.metadata["resume_count"] = min(
                    32, int(target.metadata.lookup("resume_count", 0)) + 1
                )
                flow.store.record_write(MetadataUpdate(resumed_ref, MetadataAspect.GENERATOR_STATE))
                if flow.kind == "suspend":
                    active_exception = self.t.exception_context(flow.store)
                    owner = active_exception.owner.exact_constants()
                    if (
                        owner == [generator_frame.identity]
                        or active_exception.owner.top
                        or active_exception.owner.unbound
                    ):
                        target.metadata["owned_exception"] = active_exception
                    else:
                        target.metadata.pop("owned_exception", None)
                    self.t.install_exception_context(flow.store, caller_exception)
                    target.metadata.update(stage="suspended", remaining=flow.continuation)
                    result.append(
                        self.t.error(flow.store, "RuntimeError")
                        if operation == "close"
                        else NormalExprFlow(StateHandle.capture(flow.store), require_payload(flow))
                    )
                else:
                    target.metadata["stage"] = "closed"
                    target.metadata.pop("owned_exception", None)
                    self.t.install_exception_context(flow.store, caller_exception)
                    if operation == "close" and (
                        flow.kind in {"normal", "return"}
                        or flow.kind == "raise"
                        and flow.exception in {"GeneratorExit", "StopIteration"}
                    ):
                        result.append(self.normal(flow.store))
                    elif flow.kind in {"normal", "return"}:
                        returned = (
                            require_payload(flow) if flow.kind == "return" else Value.constant(None)
                        )
                        result.append(
                            NormalExprFlow(StateHandle.capture(flow.store), returned)
                            if operation == "await"
                            else self.stop(flow.store, frame, node, returned)
                        )
                    elif self.is_stop(flow):
                        result.append(self.t.error(flow.store, "RuntimeError"))
                    else:
                        result.append(flow)
        if receiver.top or receiver.top_sorts:
            opaque = self.t.unknown(store, node, "unknown_resume")
            result.extend(opaque)
            if operation == "await":
                result.extend(
                    SuspendFlow(
                        StateHandle.capture(out.store.copy()),
                        Value.unknown(),
                        FlowTail(
                            continuation=Continuation(
                                (OpaqueResume("external_await_resume"),),
                                ContinuationId(f"{self.d.current}:{self.d.point(node)}:await"),
                                self.d.point(node),
                            )
                        ),
                    )
                    for out in opaque
                    if out.kind == "normal"
                )
        return self.d.compact(result, execution=self.t.execution)

    def generator_expression(
        self, node: ast.GeneratorExp, store: Store, frame: Frame
    ) -> list[Flow]:
        result: list[Flow]
        first = node.generators[0]
        result = []
        for outer in self.t.expr(first.iter, store, frame):
            if outer.kind != "normal":
                result.append(outer)
                continue
            for iterator in self.get_iterator(
                require_payload(outer), outer.store, frame, node, bool(first.is_async)
            ):
                if iterator.kind != "normal":
                    result.append(iterator)
                    continue
                current = iterator.store
                identity = FrameId(f"genexpr-frame:{self.d.point(node)}@{'|'.join(frame.context)}")
                locals_ = frozenset(
                    {"$outer_iterator"}
                    | {
                        item.id
                        for gen in node.generators
                        for item in ast.walk(gen.target)
                        if isinstance(item, ast.Name)
                    }
                )
                closure = dict(frame.closure)
                if not frame.is_module and not frame.is_class:
                    closure.update((name, frame.cell(name)) for name in frame.locals)
                inner: list[ast.stmt] = [ast.Expr(value=ast.Yield(value=node.elt))]
                for position, gen in reversed(list(enumerate(node.generators))):
                    for condition in reversed(gen.ifs):
                        inner = [ast.If(test=condition, body=inner, orelse=[])]
                    cls = ast.AsyncFor if gen.is_async else ast.For
                    inner = [
                        cls(
                            target=gen.target,
                            iter=ast.Name(id="$outer_iterator", ctx=ast.Load())
                            if position == 0
                            else gen.iter,
                            body=inner,
                            orelse=[],
                            type_comment=None,
                        )
                    ]
                for index, item in enumerate(ast.walk(inner[0])):
                    if id(item) not in self.d.nodes:
                        self.d.register_generated(item, self.d.point(node) + f"#genexpr:{index}")
                compframe = Frame(
                    frame.module,
                    frame.qualname + ".<genexpr>",
                    frame.context,
                    identity,
                    locals_,
                    tuple(closure.items()),
                )
                entry = current.clear_effects()
                entry.cells[compframe.cell("$outer_iterator")] = require_payload(iterator)
                function_id = ObjectId(
                    "genexpr-function:" + self.d.point(node) + "@" + "|".join(frame.context)
                )
                function_node = ast.FunctionDef(
                    name="<genexpr>",
                    args=ast.arguments(
                        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
                    ),
                    body=inner,
                    decorator_list=[],
                    type_params=[],
                )
                self.d.register_generated(function_node, self.d.point(node) + "#genexpr-function")
                self.d.functions[function_id] = FunctionInfo(
                    function_id,
                    function_node,
                    frame.module,
                    compframe.qualname,
                    tuple(closure.items()),
                    (),
                    (),
                    locals_,
                    frozenset(),
                    frozenset(),
                    True,
                    False,
                )
                metadata = {
                    "function": function_id,
                    "frame": compframe,
                    "entry": StateHandle.capture(entry),
                    "body": tuple(inner),
                    "stage": "created",
                    "index": 0,
                }
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(current),
                        self.t.alloc(node, current, frame, "generator", metadata=metadata),
                    )
                )
        return result
