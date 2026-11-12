"""Engine-neutral runtime precision/work budget declarations.

This is the single definition of the historical 25-field AnalysisBudget. Public
compatibility modules re-export the same class; no defaults or ranges change.
A shared runtime WorkLedger accounts work across engine boundaries independently
of this configuration description.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass
class AnalysisBudget:
    """Budget limits for AI analysis to ensure termination.

    Attributes:
        max_iterations: Maximum fixpoint iterations
        max_stmt_visits: Maximum statement visits per block
        max_recursion_depth: Maximum recursive AI calls
        strict_exceptions: Re-raise internal exceptions instead of masking
        max_addr_set_size: Cap for address set widening
        max_field_keys: Cap for field store key tracking
        max_const_strings: Cap for string domain const set
        max_ho_tokens: Cap for higher-order token set
        enable_narrowing: Whether to run narrowing pass
        narrowing_iterations: Number of narrowing iterations

        Value Partitioning:
        enable_value_partition: Whether to refine states on branches
        max_value_partitions: Max partitions per variable before merging
        value_partition_str_cap: Max string constants to enumerate for partitioning

        Tuple/Int Indexing:
        max_const_ints: Max int constants to enumerate for subscript access
        max_tuple_len_exact: Max tuple length to materialize exactly
        max_tuple_alts: Max tuple shape alternatives before merging
        max_star_expand_len: Max tuple length for *args expansion
        max_star_expand_alts: Max tuple alternatives for *args expansion

        Dict Modeling (Route A):
        max_dict_parts: Max key partitions per dict before spilling to default
        max_dict_key_const_strs: Max constant strings to track as distinct keys
        max_dict_key_const_ints: Max constant ints to track as distinct keys
        max_dict_key_tuple_len: Max tuple length for tuple key decomposition
        max_dict_key_tuple_alts: Max tuple alternatives for tuple keys
        enable_dict_identity_keys: Enable identity-based key partitioning
        max_dict_identity_keys: Max identity keys before spilling to default
    """

    max_iterations: int = 100
    max_stmt_visits: int = 50
    max_recursion_depth: int = 3
    strict_exceptions: bool = False
    max_addr_set_size: int = 50
    max_field_keys: int = 20
    max_const_strings: int = 10
    max_ho_tokens: int = 20
    enable_narrowing: bool = True
    narrowing_iterations: int = 2

    # Value partitioning settings
    enable_value_partition: bool = True  # Enable branch refinement (on by default)
    max_value_partitions: int = 4  # Max partitions per variable
    value_partition_str_cap: int = 4  # Max strings to enumerate for partitioning

    # Tuple/int indexing settings
    max_const_ints: int = 4  # Max int constants for subscript (e.g., t[0], t[1])
    max_tuple_len_exact: int = 8  # Max tuple length to materialize exactly
    max_tuple_alts: int = 4  # Max tuple shape alternatives before merging
    max_star_expand_len: int = 8  # Max tuple length for *args expansion
    max_star_expand_alts: int = 4  # Max tuple alternatives for *args expansion

    # Dict modeling settings (Route A)
    max_dict_parts: int = 20  # Max key partitions per dict
    max_dict_key_const_strs: int = 10  # Max constant string keys to track
    max_dict_key_const_ints: int = 10  # Max constant int keys to track
    max_dict_key_tuple_len: int = 4  # Max tuple length for tuple key decomposition
    max_dict_key_tuple_alts: int = 2  # Max tuple alternatives for tuple keys
    enable_dict_identity_keys: bool = True  # Enable identity key partitioning
    max_dict_identity_keys: int = 10  # Max identity keys

    def to_dict(self) -> dict[str, int | bool]:
        """Return the finite, owned configuration projection."""
        return {
            "max_iterations": self.max_iterations,
            "max_stmt_visits": self.max_stmt_visits,
            "max_recursion_depth": self.max_recursion_depth,
            "strict_exceptions": self.strict_exceptions,
            "max_addr_set_size": self.max_addr_set_size,
            "max_field_keys": self.max_field_keys,
            "max_const_strings": self.max_const_strings,
            "max_ho_tokens": self.max_ho_tokens,
            "enable_narrowing": self.enable_narrowing,
            "narrowing_iterations": self.narrowing_iterations,
            "enable_value_partition": self.enable_value_partition,
            "max_value_partitions": self.max_value_partitions,
            "value_partition_str_cap": self.value_partition_str_cap,
            "max_const_ints": self.max_const_ints,
            "max_tuple_len_exact": self.max_tuple_len_exact,
            "max_tuple_alts": self.max_tuple_alts,
            "max_star_expand_len": self.max_star_expand_len,
            "max_star_expand_alts": self.max_star_expand_alts,
            "max_dict_parts": self.max_dict_parts,
            "max_dict_key_const_strs": self.max_dict_key_const_strs,
            "max_dict_key_const_ints": self.max_dict_key_const_ints,
            "max_dict_key_tuple_len": self.max_dict_key_tuple_len,
            "max_dict_key_tuple_alts": self.max_dict_key_tuple_alts,
            "enable_dict_identity_keys": self.enable_dict_identity_keys,
            "max_dict_identity_keys": self.max_dict_identity_keys,
        }


def validate_budget_options(options: Mapping[str, object]) -> dict[str, int | bool]:
    """Validate historical budget controls without truthiness or bool/int coercion."""
    if type(options) is not dict:
        raise ValueError("AI budget must be a dictionary")
    defaults = AnalysisBudget().to_dict()
    unknown = set(options) - defaults.keys()
    if unknown:
        raise ValueError(f"Unknown AI budget options: {sorted(unknown)}")
    zero_allowed = {
        "max_iterations",
        "max_stmt_visits",
        "max_recursion_depth",
        "narrowing_iterations",
        "max_value_partitions",
    }
    checked: dict[str, int | bool] = {}
    for name, value in options.items():
        default = defaults[name]
        if isinstance(default, bool):
            if type(value) is not bool:
                raise ValueError(f"{name} must have type bool")
            checked[name] = value
        else:
            if type(value) is not int:
                raise ValueError(f"{name} must have type int")
            if value < (0 if name in zero_allowed else 1):
                raise ValueError(f"{name} is outside its supported range")
            checked[name] = value
    return checked
