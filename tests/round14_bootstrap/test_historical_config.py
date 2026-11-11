"""Historical schemas stay readable without erasing explicit new policies."""
import pytest

from benchmark.round8_corpus import collect_results
from tests.round10_empirical.test_round10_historical_schema_and_progress import (
    historical_langserve, mutate_historical,
)


def test_original_bound_evidence_retains_its_actual_effective_schema(historical_langserve):
    directory, records = historical_langserve
    result = collect_results.static_results(directory, records)
    assert result["trials"] == 2
    assert all(row["whole_program_soundness_proved"] is False for row in result["rows"])


@pytest.mark.parametrize("option,value", [
    ("builtin_bootstrap_policy", "queued"),
    ("builtin_bootstrap_policy", "eager_inert"),
    ("keyword_context_model", "none"),
    ("keyword_context_model", "receiver_key_v1"),
    ("keyword_context_limit", 1),
    ("keyword_context_limit", 32),
])
def test_new_explicit_input_cannot_be_erased_to_match_old_worker_digest(historical_langserve, option, value):
    directory, records = historical_langserve
    mutate_historical(directory, lambda raw: raw["configuration"].update({option: value}))
    with pytest.raises(ValueError, match="configuration binding"):
        collect_results.static_results(directory, records)
