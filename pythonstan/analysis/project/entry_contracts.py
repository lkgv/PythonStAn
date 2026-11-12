"""Bounded external-call templates from represented exported function values.

A source declaration selected by qualified name is not a callable signature:
decorators and rebinding can replace that declaration with a different value.
This helper reads the current points-to snapshot. It neither manufactures a
receiver nor claims exhaustive external inputs or continuation execution.
"""

from __future__ import annotations

import ast
import keyword
from collections.abc import Iterable
from dataclasses import dataclass, field

from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, FunctionObject
from pythonstan.ir import IRFunc


@dataclass(frozen=True, order=True)
class FunctionOrigin:
    qualified_name: str
    line: int
    column: int
    end_line: int
    is_async: bool

    def to_dict(self) -> JsonObject:
        return {
            "qualified_name": self.qualified_name,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "is_async": self.is_async,
        }


@dataclass(frozen=True)
class FunctionArgumentTemplate:
    positional_only: tuple[str, ...]
    positional_or_keyword: tuple[str, ...]
    keyword_only: tuple[str, ...]
    vararg: str | None
    kwarg: str | None
    origins: tuple[FunctionOrigin, ...]
    # These are actual values observed in the export snapshot, never source
    # objects reconstructed from a qualified-name lookup. An integration may
    # use them to keep each argument shape correlated with its represented
    # callees rather than calling every export alternative with every shape.
    functions: tuple[FunctionObject, ...] = field(repr=False, compare=False)
    origin_node: ast.FunctionDef | ast.AsyncFunctionDef = field(repr=False, compare=False)

    def to_dict(self) -> JsonObject:
        return {
            "positional_only": list(self.positional_only),
            "positional_or_keyword": list(self.positional_or_keyword),
            "keyword_only": list(self.keyword_only),
            "vararg": self.vararg,
            "kwarg": self.kwarg,
            "source_definitions": [origin.to_dict() for origin in self.origins],
            "represented_function_values": len(self.functions),
            "parameter_coverage": "unknown_witnesses_not_exhaustive",
            "variadic_coverage_proved": False,
            "default_metadata_coverage_proved": False,
            "async_resume_coverage_proved": False,
        }


@dataclass(frozen=True, order=True)
class UncoveredExportValue:
    object_type: str
    reason: str
    count: int

    def to_dict(self) -> JsonObject:
        return {"object_type": self.object_type, "reason": self.reason, "count": self.count}


@dataclass(frozen=True)
class ExportTemplatePlan:
    templates: tuple[FunctionArgumentTemplate, ...]
    uncovered: tuple[UncoveredExportValue, ...]
    represented_values: int

    def to_dict(self) -> JsonObject:
        return {
            "origin": "actual_exported_points_to_snapshot",
            "represented_values": self.represented_values,
            "templates": [template.to_dict() for template in self.templates],
            "uncovered_values": [value.to_dict() for value in self.uncovered],
            "empty_binding_observed": self.represented_values == 0,
            "exhaustive_export_binding_proved": False,
            "entry_argument_coverage_proved": False,
            "runtime_code_metadata_stability_proved": False,
            "async_resume_coverage_proved": False,
        }


type ArgumentShape = tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...], str | None, str | None
]
type OriginRow = tuple[FunctionOrigin, FunctionObject, ast.FunctionDef | ast.AsyncFunctionDef]


def _signature(node: ast.AST) -> ArgumentShape | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if (
        type(node) not in (ast.FunctionDef, ast.AsyncFunctionDef)
        or type(node.args) is not ast.arguments
    ):
        return None
    args = node.args
    groups = (args.posonlyargs, args.args, args.kwonlyargs)
    if any(
        type(group) is not list or any(type(arg) is not ast.arg for arg in group)
        for group in groups
    ):
        return None
    if any(arg is not None and type(arg) is not ast.arg for arg in (args.vararg, args.kwarg)):
        return None
    names = [arg.arg for group in groups for arg in group]
    names.extend(arg.arg for arg in (args.vararg, args.kwarg) if arg is not None)
    if any(
        type(name) is not str or not name.isidentifier() or keyword.iskeyword(name)
        for name in names
    ) or len(set(names)) != len(names):
        return None
    return (
        tuple(arg.arg for arg in args.posonlyargs),
        tuple(arg.arg for arg in args.args),
        tuple(arg.arg for arg in args.kwonlyargs),
        args.vararg.arg if args.vararg else None,
        args.kwarg.arg if args.kwarg else None,
    )


def derive_exported_function_templates(points: Iterable[AbstractObject]) -> ExportTemplatePlan:
    """Describe finite call witnesses for exact represented function values.

    Bound methods, callable instances/classes, builtins, unknown values and
    unrecognized function representations remain uncovered. The result is a
    snapshot; newly discovered export alternatives or runtime mutations need
    separate contracts. Identical shapes may share a template, but all actual
    represented functions and source origins remain attached to that shape.
    """
    grouped: dict[ArgumentShape, list[OriginRow]] = {}
    uncovered: dict[tuple[str, str], int] = {}
    count = 0
    reason: str | None
    for obj in points:
        count += 1
        if type(obj) is not FunctionObject:
            reason = "not_an_exact_unbound_source_function_value"
        else:
            # Native objects are mutable metadata carriers. Keep the runtime
            # boundary check even when the nominal field annotation is IRFunc.
            source_ir: object = obj.ir
            reason = None if isinstance(source_ir, IRFunc) else "unrecognized_source_function_ir"
        if reason is not None:
            key = (type(obj).__name__, reason)
            uncovered[key] = uncovered.get(key, 0) + 1
            continue
        if not isinstance(obj, FunctionObject):
            raise AssertionError("exact source function was not narrowed")
        node = obj.ir.get_ast()
        signature = _signature(node)
        if signature is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            key = (type(obj).__name__, "unrecognized_source_argument_shape")
            uncovered[key] = uncovered.get(key, 0) + 1
            continue

        def position(name: str, origin_node: ast.AST = node) -> int:
            value: object = getattr(origin_node, name, -1)
            return value if type(value) is int else -1

        origin = FunctionOrigin(
            obj.ir.get_qualname(),
            position("lineno"),
            position("col_offset"),
            position("end_lineno"),
            isinstance(node, ast.AsyncFunctionDef),
        )
        grouped.setdefault(signature, []).append((origin, obj, node))
    templates = []

    # Optional names use an explicit string sort sentinel; None and str are
    # not mutually orderable in Python. Parameter tuples preserve order.
    def shape_key(
        signature: ArgumentShape,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str, str]:
        return (
            signature[0],
            signature[1],
            signature[2],
            signature[3] or "",
            signature[4] or "",
        )

    def origin_key(row: OriginRow) -> FunctionOrigin:
        return row[0]

    for signature in sorted(grouped, key=shape_key):
        rows = sorted(grouped[signature], key=origin_key)
        origins = tuple(sorted({row[0] for row in rows}))
        templates.append(
            FunctionArgumentTemplate(*signature, origins, tuple(row[1] for row in rows), rows[0][2])
        )
    return ExportTemplatePlan(
        tuple(templates),
        tuple(UncoveredExportValue(*key, value) for key, value in sorted(uncovered.items())),
        count,
    )
