"""Versioned software fuel; wall and sampled memory are separate resources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Hashable
from types import TracebackType

from .json_data import JsonObject

COST_MODEL_VERSION = "logical-work-v1"
STRUCTURAL_OPERATIONS_PER_TICK = 8


def cost_model_document() -> JsonObject:
    """Return an owned descriptor of the fixed accounting policy."""
    return {
        "version": COST_MODEL_VERSION,
        "semantic_operation_ticks": 1,
        "structural_operations_per_tick": STRUCTURAL_OPERATIONS_PER_TICK,
        "structural_rounding": "precharge_each_operation_batch_upward",
        "surplus_credit": "discard_at_operation_end",
        "scope_credit_transfer": False,
        "raw_primitive_counts_retained": True,
        "raw_count_semantics": "admitted_precharged_visits_not_completed_cpu_operations",
        "json_text_unit": "one_node_plus_one_per_started_UTF8_surrogatepass_KiB",
        "json_object_key_unit": "same_as_json_text_value",
        "native_wire_unit": "one_tick_plus_one_per_started_KiB_per_frame",
        "physical_cpu_time_bound": False,
    }


COST_MODEL_DIGEST = hashlib.sha256(
    json.dumps(cost_model_document(), sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def structural_ticks(raw_operations: int) -> int:
    if type(raw_operations) is not int or raw_operations < 0:
        raise ValueError("Structural operation count must be a nonnegative integer")
    return (raw_operations + STRUCTURAL_OPERATIONS_PER_TICK - 1) // STRUCTURAL_OPERATIONS_PER_TICK


def visit_json_text(text: str, visit: Callable[[int], None]) -> None:
    """Admit a node and byte blocks without allocating an encoded copy."""
    visit(1)
    byte_count = 0
    admitted_blocks = 0
    for character in text:
        codepoint = ord(character)
        width = (
            1 if codepoint < 0x80 else 2 if codepoint < 0x800 else 3 if codepoint < 0x10000 else 4
        )
        blocks = (byte_count + width + 1023) // 1024
        if blocks > admitted_blocks:
            visit(blocks - admitted_blocks)
            admitted_blocks = blocks
        byte_count += width


class StructuralWorkMeter:
    """Precharge one operation's batches; keep no cross-operation credit.

    The charger must reject atomically, without a debit. Raw counters describe
    admitted visits and do not assert that an interrupted copy completed.
    """

    def __init__(
        self,
        charge: Callable[[int, str], None],
        record: Callable[[str, int], None],
        category: str,
        *,
        scope: Callable[[], Hashable] | None = None,
    ) -> None:
        if not isinstance(category, str) or not category:
            raise ValueError("A structural work category is required")
        self._charge = charge
        self._record = record
        self._category = category
        self._scope = scope
        self._scope_identity: Hashable = None if scope is None else scope()
        self._raw_operations = 0
        self._charged_ticks = 0
        self._closed = False
        self._entered = False
        self._busy = False

    @property
    def raw_operations(self) -> int:
        return self._raw_operations

    @property
    def charged_ticks(self) -> int:
        return self._charged_ticks

    def visit(self, raw_ops: int = 1) -> None:
        if type(raw_ops) is not int or raw_ops < 0:
            raise ValueError("Structural operation count must be a nonnegative integer")
        if self._closed or self._busy:
            raise RuntimeError("Structural work operation is closed or reentrant")
        if self._scope is not None and self._scope() != self._scope_identity:
            raise RuntimeError("Structural work operation belongs to another scope")
        total = self._raw_operations + raw_ops
        charged = structural_ticks(total)
        self._busy = True
        try:
            # Zero-delta checks retain deadline and fault-injection boundaries.
            self._charge(charged - self._charged_ticks, self._category)
            self._raw_operations = total
            self._charged_ticks = charged
            self._record(self._category, raw_ops)
        finally:
            self._busy = False

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> StructuralWorkMeter:
        if self._closed or self._entered:
            raise RuntimeError("Structural work operation cannot be entered again")
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
