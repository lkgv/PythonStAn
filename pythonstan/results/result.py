"""Detached immutable result records and their read-only public adapters."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Protocol, override

from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_json_object,
    require_array,
    require_object,
    require_string,
    thaw_json,
    thaw_json_object,
    validate_json,
    validate_json_object,
)

from .output import OutputSpec, parse_output
from .projection import ProjectedPayload, module_for_qualname, project_payload, project_value_query
from .queries import (
    PropertyQuery,
    QueryInput,
    QuerySpec,
    ValueQuery,
    ViewQuery,
    parse_queries,
    parse_query,
    query_to_wire,
    resolved_query_key,
)


def _frozen_object(value: FrozenJsonValue) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise TypeError("Expected retained JSON object")
    return value


def _frozen_array(value: FrozenJsonValue) -> tuple[FrozenJsonValue, ...]:
    if not isinstance(value, tuple):
        raise TypeError("Expected retained JSON array")
    return value


def _frozen_string(value: FrozenJsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected retained JSON string")
    return value


@dataclass(frozen=True)
class PublishedSnapshot:
    """Checked publication boundary; facts remain immutable JSON wire values."""

    document: FrozenJsonObject
    solver_status: str
    coverage_status: str
    proof_status: str
    actual_engine: str

    @classmethod
    def from_wire(cls, data: object) -> PublishedSnapshot:
        document = freeze_json_object(validate_json_object(data))
        return cls(
            document,
            _frozen_string(document["solver_status"]),
            _frozen_string(document["coverage_status"]),
            _frozen_string(document.get("proof_status", "not_requested")),
            _frozen_string(document["actual_engine"]),
        )


@dataclass(frozen=True)
class QueryAnswer(Mapping[str, JsonValue]):
    query: FrozenJsonObject
    status: str
    _payload: FrozenJsonValue
    approximation: str = "flow_sensitive_may"
    coverage_status: str = "open"
    proof_status: str = "not_requested"

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", freeze_json_object(validate_json_object(self.query)))
        object.__setattr__(self, "_payload", freeze_json(validate_json(self._payload)))

    @classmethod
    def create(
        cls,
        query: QuerySpec,
        status: str,
        payload: FrozenJsonValue,
        approximation: str,
        coverage_status: str,
        proof_status: str = "not_requested",
    ) -> QueryAnswer:
        return cls(
            freeze_json_object(query_to_wire(query)),
            status,
            payload,
            approximation,
            coverage_status,
            proof_status,
        )

    @property
    def value(self) -> JsonValue:
        return thaw_json(self._payload)

    @property
    def answer(self) -> JsonValue:
        return self.value

    def to_dict(self) -> JsonObject:
        return {
            "query": thaw_json_object(self.query),
            "status": self.status,
            "value": self.value,
            "approximation": self.approximation,
            "coverage_status": self.coverage_status,
            "proof_status": self.proof_status,
        }

    @override
    def __getitem__(self, key: str) -> JsonValue:
        return self.to_dict()[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    @override
    def __len__(self) -> int:
        return len(self.to_dict())


class TextDestination(Protocol):
    def write(self, text: str, /) -> object: ...


def _resolved_queries(queries: Sequence[QueryInput]) -> tuple[QuerySpec, ...]:
    """Keep the actual resolved run untouched; parse only legacy external construction."""
    result: list[QuerySpec] = []
    for query in queries:
        if not isinstance(query, (ValueQuery, PropertyQuery, ViewQuery)):
            return parse_queries(queries)
        result.append(query)
    return tuple(result)


class AnalysisResult:
    """Published facts stay independent of session lifetime and later runs."""

    _snapshot: PublishedSnapshot
    _queries: tuple[QuerySpec, ...]
    _query_keys: frozenset[str]
    output: OutputSpec

    def __init__(
        self,
        data: object,
        queries: Sequence[QueryInput],
        output: OutputSpec | None = None,
    ) -> None:
        self._snapshot = PublishedSnapshot.from_wire(data)
        self._queries = _resolved_queries(queries)
        self._query_keys = frozenset(resolved_query_key(query) for query in self._queries)
        if output is None:
            config = _frozen_object(self._snapshot.document["effective_config"])
            self.output = parse_output(thaw_json(config["output"]))
        else:
            self.output = output

    @property
    def solver_status(self) -> str:
        return self._snapshot.solver_status

    @property
    def coverage_status(self) -> str:
        return self._snapshot.coverage_status

    @property
    def proof_status(self) -> str:
        return self._snapshot.proof_status

    @property
    def effective_config(self) -> JsonObject:
        return thaw_json_object(_frozen_object(self._snapshot.document["effective_config"]))

    @property
    def phase_trace(self) -> list[JsonValue]:
        return require_array(thaw_json(self._snapshot.document.get("phase_trace", ())))

    @property
    def actual_engine(self) -> str:
        return self._snapshot.actual_engine

    @property
    def is_complete(self) -> bool:
        """Compatibility only: declared convergence/coverage, independent of proof."""
        return self.solver_status == "converged" and self.coverage_status == "closed_under_profile"

    def query(self, query: QueryInput) -> QueryAnswer:
        return self._query_resolved(parse_query(query))

    def _query_resolved(self, query: QuerySpec) -> QueryAnswer:
        requested = query.kind == "summary" or resolved_query_key(query) in self._query_keys
        if isinstance(query, ValueQuery):
            view = ViewQuery("values" if query.point == "module_exit" else "returns")
            requested = requested or resolved_query_key(view) in self._query_keys
        approximation = (
            "flow_insensitive_may" if self.actual_engine == "native" else "flow_sensitive_may"
        )
        if not requested:
            return QueryAnswer.create(
                query, "not_requested", None, approximation, self.coverage_status
            )
        data = self._snapshot.document
        status = "answered"
        payload: FrozenJsonValue
        if isinstance(query, ValueQuery):
            if query.point == "module_exit":
                values = _frozen_object(data.get("values", {}))
                payload = _frozen_object(values.get(query.module, {})).get(query.symbol)
            else:
                payload = _frozen_object(data.get("returns", {})).get(
                    query.module + "." + query.symbol
                )
            if payload is None:
                status = "unavailable"
            elif (
                isinstance(payload, Mapping)
                and payload.get("bottom")
                and self.solver_status != "converged"
            ):
                payload, status = None, "unavailable"
        elif isinstance(query, PropertyQuery):
            proof = _frozen_object(data.get("proof", {}))
            queries = _frozen_object(proof.get("queries", {}))
            properties = _frozen_object(proof.get("properties", {}))
            payload = queries.get(resolved_query_key(query)) or properties.get(query.property)
            if payload is None:
                payload = freeze_json(
                    {
                        "status": "unknown",
                        "reason": "No closed certificate for the requested property",
                    }
                )
        elif query.kind == "relations":
            payload = freeze_json(self._relations())
        elif query.kind == "summary":
            payload = freeze_json(
                {
                    "modules": len(_frozen_array(data.get("inventory", ()))),
                    "entry_contract": thaw_json(data["entry_contract"]),
                    "calls": len(_frozen_array(data.get("calls", ()))),
                    "obligations": len(_frozen_array(data.get("obligations", ()))),
                    "actual_engine": self.actual_engine,
                    "solver_status": self.solver_status,
                    "coverage_status": self.coverage_status,
                    "proof_status": self.proof_status,
                }
            )
        else:
            payload = data.get(query.kind)
            if payload is None:
                status = "unavailable"
        proof_status = "not_requested"
        if isinstance(query, PropertyQuery):
            proof_status = _frozen_string(_frozen_object(payload).get("status", "unknown"))
        elif query.kind == "proof":
            proof_status = self.proof_status
        return QueryAnswer.create(
            query, status, payload, approximation, self.coverage_status, proof_status
        )

    def _relations(self) -> list[JsonValue]:
        data = self._snapshot.document
        rows: list[JsonValue] = []
        for module, value in _frozen_object(data.get("values", {})).items():
            for symbol, raw_value in _frozen_object(value).items():
                for relation in _frozen_array(_frozen_object(raw_value).get("relations", ())):
                    row = thaw_json_object(_frozen_object(relation))
                    row.update(
                        module=module,
                        symbol=symbol,
                        qualname=module + "." + symbol,
                        observation_point="module_exit",
                    )
                    rows.append(row)
        mutable = self.raw_snapshot()
        for function, value in _frozen_object(data.get("returns", {})).items():
            for relation in _frozen_array(_frozen_object(value).get("relations", ())):
                row = thaw_json_object(_frozen_object(relation))
                row.update(
                    module=module_for_qualname(function, mutable),
                    qualname=function,
                    observation_point="function_exit",
                )
                rows.append(row)
        return rows

    def raw_snapshot(self) -> JsonObject:
        return thaw_json_object(self._snapshot.document)

    @staticmethod
    def _projected_answer(
        answer: QueryAnswer, projected: ProjectedPayload, *, proof: bool
    ) -> JsonObject:
        wire = answer.to_dict()
        wire["value"] = projected.value
        if projected.status is not None:
            wire["status"] = projected.status
            wire["reason"] = projected.reason
            if proof:
                wire["proof_status"] = "unknown"
        return wire

    def to_dict(self, output: OutputSpec | Mapping[str, object] | None = None) -> JsonObject:
        spec = self.output if output is None else parse_output(output)
        return self._project_dict(spec)

    def _project_dict(self, spec: OutputSpec) -> JsonObject:
        data = self.raw_snapshot()
        invariant_keys = (
            "schema_version",
            "run_id",
            "source_digest",
            "model_digest",
            "policy_digest",
            "target",
            "entry_contract",
            "effective_config",
            "config_provenance",
            "inventory",
            "actual_engine",
            "phase_trace",
            "solver_status",
            "coverage_status",
            "proof_status",
            "profile",
            "diagnostics",
            "obligations",
            "evidence",
        )
        result = {key: data[key] for key in invariant_keys if key in data}
        total = count = 0
        truncated = False
        answers: JsonObject = {}
        for view in spec.views:
            answer = self._query_resolved(ViewQuery(view))
            projected = project_payload(view, answer.value, spec, data)
            answers[view] = self._projected_answer(answer, projected, proof=view == "proof")
            total += projected.total
            count += projected.count
            truncated |= projected.truncated
        for query in self._queries:
            if not isinstance(query, (ValueQuery, PropertyQuery)):
                continue
            key = resolved_query_key(query)
            if key in answers:
                continue
            answer = self._query_resolved(query)
            projected = (
                project_value_query(query, answer.value, spec)
                if isinstance(query, ValueQuery)
                else project_payload("proof", answer.value, spec, data)
            )
            answers[key] = self._projected_answer(
                answer, projected, proof=isinstance(query, PropertyQuery)
            )
            total += projected.total
            count += projected.count
            truncated |= projected.truncated
        projection: JsonObject = {
            "views": list(spec.views),
            "modules": list(spec.modules),
            "functions": list(spec.functions),
            "regions": list(spec.regions),
            "contexts": list(spec.contexts),
            "fields": list(spec.fields),
            "truncated": truncated,
            "total_items": total,
            "output_items": count,
        }
        result.update(answers=answers, projection=projection)
        return result

    def serialize(
        self, format: str | None = None, output: OutputSpec | Mapping[str, object] | None = None
    ) -> str:
        spec = self.output if output is None else parse_output(output)
        selected_format = spec.format if format is None else format
        data = self._project_dict(spec)
        if selected_format == "json":
            return (
                json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                + "\n"
            )
        if selected_format != "text":
            raise ValueError("format must be json or text")
        lines = [
            f"PythonStAn: {self.actual_engine}",
            f"solver={self.solver_status}; coverage={self.coverage_status}; proof={self.proof_status}",
        ]
        for view, item in require_object(data["answers"]).items():
            answer = require_object(item)
            lines.append(
                view
                + " ["
                + require_string(answer["status"])
                + "]: "
                + json.dumps(answer["value"], ensure_ascii=False, sort_keys=True)
            )
        if require_object(data["projection"])["truncated"]:
            lines.append("Output truncated; see projection metadata.")
        return "\n".join(lines) + "\n"

    def write(
        self,
        destination: str | PathLike[str] | TextDestination,
        format: str | None = None,
        output: OutputSpec | Mapping[str, object] | None = None,
    ) -> None:
        text = self.serialize(format=format, output=output)
        if isinstance(destination, (str, PathLike)):
            Path(destination).write_text(text, encoding="utf-8")
        else:
            destination.write(text)

    @override
    def __repr__(self) -> str:
        return f"AnalysisResult(engine={self.actual_engine!r}, solver={self.solver_status!r}, coverage={self.coverage_status!r})"
