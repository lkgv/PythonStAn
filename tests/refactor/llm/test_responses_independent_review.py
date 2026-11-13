"""Independent provider boundary checks using in-memory HTTP and isolated imports."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from pythonstan.config.llm_provider import ProviderMode, ProviderOptions
from pythonstan.llm.openai_transport import FailureCode
from pythonstan.llm.responses import ResponsesCandidateProposer


@pytest.mark.parametrize(
    "mutation,code,stopped",
    [
        ("refusal", FailureCode.REFUSAL, False),
        ("incomplete", FailureCode.INCOMPLETE, False),
        ("usage", FailureCode.MISSING_USAGE, True),
        ("model", FailureCode.WRONG_MODEL, True),
        ("timeout", FailureCode.TIMEOUT, True),
    ],
)
def test_real_sdk_codec_failures_are_redacted_and_do_not_retry(
    mutation: str, code: FailureCode, stopped: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2
    import openai

    import pythonstan.llm.openai_transport as boundary

    canary = "independent-synthetic-canary"
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://api.openai.com/v1/responses"
        if mutation == "timeout":
            raise httpx2.ReadTimeout(canary, request=request)
        data: dict[str, object] = {
            "id": "independent",
            "object": "response",
            "created_at": 0,
            "model": "unexpected-model" if mutation == "model" else "gpt-6-luna",
            "status": "incomplete" if mutation == "incomplete" else "completed",
            "output": [
                {
                    "type": "message",
                    "id": "independent-message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": canary}]
                    if mutation == "refusal"
                    else [{"type": "output_text", "text": "{}"}],
                }
            ],
        }
        if mutation != "usage":
            data["usage"] = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        return httpx2.Response(200, json=data)

    original = openai.OpenAI

    def client(*, api_key: str, base_url: str, max_retries: int, timeout: float) -> openai.OpenAI:
        assert api_key == canary and max_retries == 0
        return original(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(boundary.os, "environ", {"OPENAI_API_KEY": canary})
    monkeypatch.setattr(openai, "OpenAI", client)
    proposer = ResponsesCandidateProposer(
        replace(ProviderOptions(ProviderMode.OPENAI_LIVE), max_calls=2),
        policy_digest="independent-policy",
    )
    proposal = proposer.propose("answer=1\n")
    assert proposal.candidate is None and proposal.diagnostics == (code.value,)
    assert canary not in repr(proposal)
    assert calls == 1 and proposer.stopped is stopped
    if stopped:
        assert proposer.propose("answer=1\n").diagnostics == (FailureCode.STOPPED.value,)
        assert calls == 1


def test_fresh_disabled_process_imports_no_sdk_and_reads_no_key() -> None:
    root = Path(__file__).resolve().parents[3]
    source = f"""import sys,os,builtins
sys.path.insert(0,{str(root)!r})
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name=='openai' or name.startswith('openai.'):
        raise AssertionError('disabled SDK import')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
class Environment(dict):
    def get(self,key,default=None):
        if key=='OPENAI_API_KEY':raise AssertionError('disabled key read')
        return super().get(key,default)
os.environ=Environment()
from pythonstan import Source,analyze
r=analyze(Source('answer=1',module='disabled'),config={{'engine':'ai_primary','llm':{{'enabled':False,'provider':'openai_live'}}}})
assert r.solver_status=='converged'
assert not any(name=='openai' or name.startswith('openai.') for name in sys.modules)
print('disabled-ok')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        env={},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert completed.stdout.strip() == "disabled-ok"
