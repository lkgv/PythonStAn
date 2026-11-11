"""Explicit partial Native snapshot adapter for the staged Native→AI mode.

Only portable typed scalar positives cross this boundary. Legacy reference
hashes are not canonical new-engine object identities and therefore are not
invented as valid references. The independent AI still discovers definitions,
objects and calls from source. Empty/partial candidates never permit pruning.
"""

from __future__ import annotations

from collections.abc import Mapping

from pythonstan.analysis.contracts import freeze_snapshot, stable_digest
from pythonstan.analysis.domains.values import Value
from .oracle import IncrementalCandidateOracle


class NativeSnapshotOracle(IncrementalCandidateOracle):
    def __init__(self, snapshot, *, source_digest, profile,
                 policy_digest="", model_digest="pythonstan-builtins-v1",
                 max_work=100000):
        if not isinstance(snapshot, Mapping):
            raise TypeError("Native snapshot must be a frozen wire mapping")
        if snapshot.get("source_digest") != source_digest:
            raise ValueError("Native snapshot source identity mismatch or missing")
        if snapshot.get("profile") != profile:
            raise ValueError("Native snapshot profile mismatch or missing")
        if policy_digest and snapshot.get("policy_digest") != policy_digest:
            raise ValueError("Native snapshot policy identity mismatch or missing")
        if snapshot.get("model_digest") != model_digest:
            raise ValueError("Native snapshot model identity mismatch or missing")
        super().__init__(source_digest=source_digest, profile=profile,
                         policy_digest=policy_digest, model_digest=model_digest,
                         max_work=max_work)
        self.snapshot = freeze_snapshot(snapshot)
        self.seeded = False
        self.imported_cells = 0
        self.skipped_references = 0
        self.unknown_rows = 0
        self.phase_record = {
            "engine": "andersen", "phase": "staged_native_snapshot_adapter",
            "whole_program_prepass": True, "coverage": "partial",
        }

    def bind(self, driver, *, source_digest=None, profile=None, policy_digest=None):
        actual_source = stable_digest({name: module.digest for name, module in sorted(driver.modules.items())})
        if actual_source != self.source_digest:
            raise ValueError("Native snapshot cannot bind a different source program")
        actual_profile = driver.options.get("semantic_profile", "python-core-v1")
        if actual_profile != self.profile:
            raise ValueError("Native snapshot cannot bind a different semantic profile")
        super().bind(driver, source_digest=actual_source, profile=actual_profile,
                     policy_digest=policy_digest)
        if self.seeded:
            return
        for module, symbols in self.snapshot.get("values", {}).items():
            if module not in driver.modules or not isinstance(symbols, Mapping):
                continue
            for symbol, row in symbols.items():
                if not isinstance(symbol, str) or not isinstance(row, Mapping):
                    continue
                self.skipped_references += len(row.get("objects", ()))
                self.unknown_rows += bool(row.get("top", False))
                value = self._scalar_value(row)
                if not value.is_bottom():
                    self.kernel.add_fact(("cell", f"global:{module}:{symbol}"), value,
                                         "native_snapshot.scalar_projection")
                    self.imported_cells += 1
        self.seeded = True
        self._solve()

    def _scalar_value(self, row):
        tagged = row.get("tagged_constants")
        values = []
        if tagged is not None:
            if not isinstance(tagged, (list, tuple)):
                raise ValueError("malformed tagged Native scalar projection")
            expected = {"str": str, "int": int, "bool": bool, "float": float,
                        "none": type(None)}
            for item in tagged:
                if not isinstance(item, Mapping) or set(item) != {"type", "value"}:
                    raise ValueError("malformed Native scalar tag")
                kind, value = item["type"], item["value"]
                if kind not in expected or type(value) is not expected[kind]:
                    raise ValueError("Native scalar type/value mismatch")
                values.append(value)
        else:
            values = list(row.get("constants", ()))
            if any(type(value) not in (str, bool, int, float, type(None)) for value in values):
                raise ValueError("Native constants must be typed JSON scalars")
        return self.kernel.ops.join_all(Value.constant(value) for value in values)

    def statistics(self):
        stats = super().statistics()
        stats.update({
            "kind": "staged_native_snapshot", "native_prepass": True,
            "native_solver_status": self.snapshot.get("solver_status", "unknown"),
            "native_work": self.snapshot.get("stats", {}).get("native_work", 0),
            "imported_scalar_cells": self.imported_cells,
            "unmapped_reference_candidates": self.skipped_references,
            "unknown_rows_not_used_as_exclusion": self.unknown_rows,
            "coverage": "partial", "permits_exclusion": False,
        })
        return stats
