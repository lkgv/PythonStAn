"""Declared builtin exception construction must not become an arbitrary call."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("case,expected",[("exception_args","known"),("exception_keyword","token"),
    ("exception_default_attr",None),("exception_stopvalue",7),("exception_shadow",9)])
def test_native_exception_values_attrs_and_shadowed_source_calls(case,expected):
    command=[sys.executable,str(ROOT/"scripts/refactor/native_context_probe.py"),"--repo",str(ROOT),
             "--case",case,"--budget","6000","--policy","2-cfa"]
    result=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    row=json.loads(result.stdout);assert row["status"]=="ok",row
    assert row["statistics"]["converged"]==1,row
    assert not row["unknown_effect_events"],row
    values={entry["value"] for entry in row["values"]["result"] if entry["kind"]=="ConstantObject"}
    assert values=={expected},row
    witness=subprocess.run([sys.executable,"-I","-c",row["source"]+"\nprint(repr(result))\n"],capture_output=True,text=True,timeout=5)
    assert witness.returncode==0 and witness.stdout.strip()==repr(expected),witness.stderr
