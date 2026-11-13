"""Sequential comprehension cursors and internal typed container builders."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.identity import FrameId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.flow import Flow, require_payload
from pythonstan.analysis.state.store import Frame, Store

from .containers import ContainerBuilder, ContainerConstruction

if TYPE_CHECKING:
    from pythonstan.analysis.engines.abstract.transfer import Transfer

type Comprehension = ast.ListComp | ast.SetComp | ast.DictComp


@dataclass(frozen=True)
class _Cursor:
    builder: ContainerBuilder
    iterator: Value


class ComprehensionEvaluation:
    def __init__(self, transfer: Transfer) -> None:
        self.t = transfer
        self.builders = ContainerConstruction(transfer)

    def _source(self, node: ast.expr, path: tuple[int, ...]) -> ast.expr:
        """Bound repeated real expression sites, preserving source coordinates."""
        with self.t.d.resource_ledger.structural_meter("comprehension_source_expansion") as meter:
            for _ in ast.walk(node):
                meter.visit(1)
        copied = deepcopy(node)
        signature = ":".join(str(min(index, 31)) for index in path)
        for index, (original, expanded) in enumerate(
            zip(ast.walk(node), ast.walk(copied), strict=True)
        ):
            self.t.d.register_generated(
                expanded, f"{self.t.d.point(original)}#comprehension:{signature}:{index}"
            )
        return copied

    def _filters(
        self,
        conditions: list[ast.expr],
        builder: ContainerBuilder,
        frame: Frame,
        path: tuple[int, ...],
    ) -> tuple[list[ContainerBuilder], list[ContainerBuilder], list[Flow]]:
        accepted = [builder]
        skipped: list[ContainerBuilder] = []
        errors: list[Flow] = []
        for condition in conditions:
            following: list[ContainerBuilder] = []
            source = self._source(condition, path)
            for current in accepted:
                for tested in self.t.condition_flows(source, current.store, frame, publish=False):
                    if tested.kind != "normal":
                        errors.append(tested)
                        continue
                    truths = require_payload(tested).bools
                    if True in truths:
                        following.append(self.builders.rebind_builder(current, tested.store.copy()))
                    if False in truths:
                        skipped.append(self.builders.rebind_builder(current, tested.store.copy()))
            accepted = following
        return accepted, skipped, errors

    def _element(
        self, node: Comprehension, builder: ContainerBuilder, frame: Frame, path: tuple[int, ...]
    ) -> tuple[list[ContainerBuilder], list[Flow]]:
        result: list[ContainerBuilder] = []
        errors: list[Flow] = []
        if isinstance(node, ast.DictComp):
            key_site = self._source(node.key, path)
            value_site = self._source(node.value, path)
            for key in self.t.expr(key_site, builder.store, frame, publish=False):
                if key.kind != "normal":
                    errors.append(key)
                    continue
                for value in self.t.expr(value_site, key.store, frame, publish=False):
                    if value.kind != "normal":
                        errors.append(value)
                        continue
                    inserted, failed = self.builders.append_builder(
                        self.builders.rebind_builder(builder, value.store),
                        require_payload(value),
                        frame,
                        value_site,
                        key=require_payload(key),
                    )
                    result.extend(inserted)
                    errors.extend(failed)
        else:
            source = self._source(node.elt, path)
            for value in self.t.expr(source, builder.store, frame, publish=False):
                if value.kind != "normal":
                    errors.append(value)
                    continue
                inserted, failed = self.builders.append_builder(
                    self.builders.rebind_builder(builder, value.store),
                    require_payload(value),
                    frame,
                    source,
                )
                result.extend(inserted)
                errors.extend(failed)
        return result, errors

    def _loop(
        self,
        node: Comprehension,
        level: int,
        builder: ContainerBuilder,
        frame: Frame,
        path: tuple[int, ...],
        *,
        outer: tuple[Value, Frame] | None = None,
    ) -> tuple[list[ContainerBuilder], list[Flow]]:
        generator = node.generators[level]
        iterable_site = self._source(generator.iter, path)
        engine = self.t.continuations
        role = f"comprehension:{level}"
        acquisition_frame = frame if outer is None else outer[1]
        iterables = (
            self.t.expr(iterable_site, builder.store, frame, publish=False)
            if outer is None
            else [self.t.normal(builder.store, outer[0])]
        )
        completed: list[ContainerBuilder] = []
        errors: list[Flow] = []
        pending: list[_Cursor] = []
        for iterable in iterables:
            if iterable.kind != "normal":
                errors.append(iterable)
                continue
            for acquired in engine.get_iterator(
                require_payload(iterable),
                iterable.store,
                acquisition_frame,
                iterable_site,
                role=role,
                publish=False,
            ):
                if acquired.kind == "normal":
                    pending.append(
                        _Cursor(
                            self.builders.rebind_builder(builder, acquired.store),
                            require_payload(acquired),
                        )
                    )
                else:
                    errors.append(acquired)
        limit = max(1, self.t.execution.integer("max_stmt_visits", 100))
        for index in range(limit):
            following: list[_Cursor] = []
            source_path = (*path, index)
            advance_site = engine.protocol_item_site(iterable_site, index, role=role + ":next")
            target = self._source(generator.target, source_path)
            for cursor in pending:
                self.t.d.charge(1, "comprehension_iteration")
                for advanced in engine.next_iterator(
                    cursor.iterator, cursor.builder.store, frame, advance_site, publish=False
                ):
                    current = self.builders.rebind_builder(cursor.builder, advanced.store)
                    if engine.is_stop(advanced):
                        completed.append(current)
                        continue
                    if advanced.kind != "normal":
                        errors.append(advanced)
                        continue
                    for assigned in self.t.assign(
                        target, require_payload(advanced), advanced.store, frame, target
                    ):
                        if assigned.kind != "normal":
                            errors.append(assigned)
                            continue
                        accepted, skipped, failed = self._filters(
                            generator.ifs,
                            self.builders.rebind_builder(current, assigned.store),
                            frame,
                            source_path,
                        )
                        errors.extend(failed)
                        following.extend(_Cursor(item, cursor.iterator) for item in skipped)
                        for item in accepted:
                            if level + 1 < len(node.generators):
                                emitted, failed = self._loop(
                                    node, level + 1, item, frame, source_path
                                )
                            else:
                                emitted, failed = self._element(node, item, frame, source_path)
                            errors.extend(failed)
                            following.extend(_Cursor(item, cursor.iterator) for item in emitted)
            pending = following
            if not pending:
                return completed, errors
            if len(pending) > self.t.execution.max_paths:
                break
        for cursor in pending:
            residual, failed = self.builders.residual_builder(
                cursor.builder, node, "comprehension_iteration_budget"
            )
            completed.extend(residual)
            errors.extend(failed)
        return completed, errors

    def evaluate(self, node: Comprehension, store: Store, frame: Frame) -> list[Flow]:
        if any(generator.is_async for generator in node.generators):
            return self.t.unknown(store, node, "async_comprehension")
        locals_ = frozenset(
            item.id
            for generator in node.generators
            for item in ast.walk(generator.target)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
        )
        identity = FrameId(f"comprehension:{self.t.d.point(node)}@{'|'.join(frame.context)}")
        closure = dict(frame.closure)
        if not frame.is_module and not frame.is_class:
            closure.update((name, frame.cell(name)) for name in frame.locals)
        compframe = Frame(
            frame.module,
            frame.qualname + ".<comprehension>",
            frame.context,
            identity,
            locals_,
            tuple(closure.items()),
            execution=self.t.execution,
        )
        kind = (
            "dict"
            if isinstance(node, ast.DictComp)
            else "set"
            if isinstance(node, ast.SetComp)
            else "list"
        )
        result: list[Flow] = []
        for iterable in self.t.expr(node.generators[0].iter, store.copy(), frame, publish=False):
            if iterable.kind != "normal":
                result.append(iterable)
                continue
            builders, errors = self._loop(
                node,
                0,
                self.builders.start_builder(kind, iterable.store),
                compframe,
                (),
                outer=(require_payload(iterable), frame),
            )
            result.extend(errors)
            result.extend(self.builders.finish_builder(item, frame, node) for item in builders)
        return self.t.d.compact(result, execution=self.t.execution)
