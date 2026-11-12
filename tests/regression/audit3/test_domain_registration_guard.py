"""The independent mandatory freeze must reject deleted or weakened fixtures."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .domain_native_acceptance import frozen_suite


@pytest.mark.parametrize("mutation", ["remove_case", "unsupported", "skip", "all_top"])
def test_domain_mandatory_freeze_rejects_downgrade(tmp_path: Path, mutation: str) -> None:
    source = Path(__file__).resolve().parents[3]
    manifest_path = Path("docs/development/next/domain_native_manifest.json")
    (tmp_path / manifest_path).parent.mkdir(parents=True)
    shutil.copy2(source / manifest_path, tmp_path / manifest_path)
    if mutation in {"remove_case", "unsupported"}:
        decoded: object = json.loads((tmp_path / manifest_path).read_text())
        assert isinstance(decoded, dict)
        if mutation == "remove_case":
            decoded["mandatory_nodes"] = []
            decoded["mandatory_node_count"] = 0
        else:
            decoded["findings"][0]["support_status"] = "unsupported"
        (tmp_path / manifest_path).write_text(json.dumps(decoded))
        with pytest.raises(ValueError, match="manifest changed"):
            frozen_suite(tmp_path)
        return
    for filename in ("domain_oracles.py", "test_domain_mandatory.py", "test_native_mandatory.py"):
        relative = Path("tests/regression/audit3") / filename
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, tmp_path / relative)
    # Make real executable downgrades, then reject them before analyzer execution.
    if mutation == "skip":
        changed = tmp_path / "tests/regression/audit3/test_domain_mandatory.py"
        changed.write_text(
            changed.read_text() + '\npytestmark = pytest.mark.skip(reason="unauthorized")\n'
        )
    else:
        changed = tmp_path / "tests/regression/audit3/domain_oracles.py"
        before = changed.read_text()
        after = before.replace(
            "            return value\n", '            return {"top": True, "top_sorts": []}\n'
        )
        assert before != after
        changed.write_text(after)
    with pytest.raises(ValueError, match="fixture changed"):
        frozen_suite(tmp_path)
