"""Partial Native scalar positives; foreign references never permit AI pruning."""

from __future__ import annotations

from collections.abc import Mapping
from typing import override

from pythonstan.analysis.contracts.identity import CellId, stable_digest
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonScalar,
    freeze_json_object,
    thaw_json,
    validate_json_object,
)
from pythonstan.analysis.contracts.oracle import OracleHostPort
from pythonstan.analysis.domains.values import Value, ValueOperations

from .oracle import IncrementalCandidateOracle


class NativeSnapshotOracle(IncrementalCandidateOracle):
    snapshot: FrozenJsonObject
    seeded: bool
    imported_cells: int
    skipped_references: int
    unknown_rows: int
    phase_record: JsonObject

    def __init__(
        self,
        snapshot: Mapping[str, object],
        *,
        source_digest: str,
        profile: str,
        policy_digest: str = "",
        model_digest: str = "pythonstan-builtins-v1",
        max_work: int = 100000,
    ) -> None:
        data = validate_json_object(snapshot)
        if data.get("source_digest") != source_digest:
            raise ValueError("Native snapshot source identity mismatch or missing")
        if data.get("profile") != profile:
            raise ValueError("Native snapshot profile mismatch or missing")
        if policy_digest and data.get("policy_digest") != policy_digest:
            raise ValueError("Native snapshot policy identity mismatch or missing")
        if data.get("model_digest") != model_digest:
            raise ValueError("Native snapshot model identity mismatch or missing")
        super().__init__(
            source_digest=source_digest,
            profile=profile,
            policy_digest=policy_digest,
            model_digest=model_digest,
            max_work=max_work,
        )
        self.snapshot = freeze_json_object(data)
        self.seeded = False
        self.imported_cells = 0
        self.skipped_references = 0
        self.unknown_rows = 0
        self.phase_record = {
            "engine": "andersen",
            "phase": "staged_native_snapshot_adapter",
            "whole_program_prepass": True,
            "coverage": "partial",
        }

    @override
    def bind(
        self,
        driver: OracleHostPort[ValueOperations],
        *,
        source_digest: str | None = None,
        profile: str | None = None,
        policy_digest: str | None = None,
    ) -> None:
        actual_source = stable_digest(
            {name: module.digest for name, module in sorted(driver.modules.items())}
        )
        if actual_source != self.source_digest:
            raise ValueError("Native snapshot cannot bind a different source program")
        actual_profile = driver.semantic_profile
        if actual_profile != self.profile:
            raise ValueError("Native snapshot cannot bind a different semantic profile")
        super().bind(
            driver, source_digest=actual_source, profile=actual_profile, policy_digest=policy_digest
        )
        if self.seeded:
            return
        values = self.snapshot.get("values")
        if values is not None and not isinstance(values, Mapping):
            raise ValueError("Native values projection must be an object")
        rows = {} if values is None else values
        for module, symbols in rows.items():
            if module not in driver.modules or not isinstance(symbols, Mapping):
                continue
            for symbol, row in symbols.items():
                if not isinstance(row, Mapping):
                    continue
                objects = row.get("objects", ())
                if not isinstance(objects, tuple):
                    raise ValueError("Native objects projection must be an array")
                self.skipped_references += len(objects)
                self.unknown_rows += bool(row.get("top", False))
                value = self._scalar_value(row)
                if not value.is_bottom():
                    self.kernel.add_fact(
                        ("cell", CellId(f"global:{module}:{symbol}")),
                        value,
                        "native_snapshot.scalar_projection",
                    )
                    self.imported_cells += 1
        self.seeded = True
        self._solve()

    def _scalar_value(self, row: FrozenJsonObject) -> Value:
        tagged = row.get("tagged_constants")
        values: list[JsonScalar] = []
        if tagged is not None:
            if not isinstance(tagged, tuple):
                raise ValueError("malformed tagged Native scalar projection")
            expected: dict[str, type[object]] = {
                "str": str,
                "int": int,
                "bool": bool,
                "float": float,
                "none": type(None),
            }
            for item in tagged:
                if not isinstance(item, Mapping) or set(item) != {"type", "value"}:
                    raise ValueError("malformed Native scalar tag")
                kind, value = item["type"], item["value"]
                if (
                    not isinstance(kind, str)
                    or kind not in expected
                    or type(value) is not expected[kind]
                ):
                    raise ValueError("Native scalar type/value mismatch")
                if value is not None and not isinstance(value, (str, bool, int, float)):
                    raise ValueError("Native constants must be typed JSON scalars")
                values.append(value)
        else:
            constants = row.get("constants", ())
            if not isinstance(constants, tuple):
                raise ValueError("Native constants projection must be an array")
            for value in constants:
                if value is not None and not isinstance(value, (str, bool, int, float)):
                    raise ValueError("Native constants must be typed JSON scalars")
                values.append(value)
        return self.kernel.ops.join_all(Value.constant(value) for value in values)

    @override
    def statistics(self) -> JsonObject:
        stats = super().statistics()
        native_stats = self.snapshot.get("stats")
        if native_stats is not None and not isinstance(native_stats, Mapping):
            raise ValueError("Native stats projection must be an object")
        native_work: FrozenJsonValue = (
            0 if native_stats is None else native_stats.get("native_work", 0)
        )
        stats.update(
            kind="staged_native_snapshot",
            native_prepass=True,
            native_solver_status=thaw_json(self.snapshot.get("solver_status", "unknown")),
            native_work=thaw_json(native_work),
            imported_scalar_cells=self.imported_cells,
            unmapped_reference_candidates=self.skipped_references,
            unknown_rows_not_used_as_exclusion=self.unknown_rows,
            coverage="partial",
            permits_exclusion=False,
        )
        return stats
