"""Concrete may-call regressions for receiver-dependent super and descriptors.

Synthetic CPython execution supplies a witness only; it is never fed into the
production analyzer. Extra may candidates are permitted, exact C3 is unproved.
"""
import pytest

from pythonstan.analysis.project import analyze_project


CASES = [
    ('''
class A:
    def run(self): return super(A, self).selected()
class B(A): pass
class C:
    def selected(self): return 61
class D(B, C): pass
result = D().run()
''', "A.run", "C.selected", 61),
    ('''
class A:
    def selected(self): return 1
class B(A):
    def run(self): return super(B, self).selected()
class C(A):
    def selected(self): return 67
class D(B, C): pass
class E(D): pass
result = E().run()
''', "B.run", "C.selected", 67),
    ('''
def callback(): return 71
class Descriptor:
    def __get__(self, receiver, owner): return callback
class A:
    def selected(self): return 1
class B(A):
    def run(self): return super().selected()
class C(A): selected = Descriptor()
class D(B, C): pass
result = D().run()
''', "B.run", "callback", 71),
    ('''
class A:
    @classmethod
    def selected(cls): return 1
class B(A):
    @classmethod
    def run(cls): return super(B, cls).selected()
class C(A):
    @classmethod
    def selected(cls): return cls.marker
class D(B, C): marker = 73
class E(D): pass
result = E.run()
''', "B.run", "C.selected", 73),
]


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("source, caller, callee, expected", CASES)
def test_actual_receiver_necessary_calls_survive_declaring_class_fallback(tmp_path, policy, source, caller, callee, expected):
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == expected
    (tmp_path / "super_cases.py").write_text(source)
    report = analyze_project({
        "project_root": str(tmp_path), "entry_file": "super_cases.py", "source_roots": ["."],
        "context_policy": policy, "enable_ai_intrinsics": False,
        "synthetic_method_contexts": False, "auto_framework_entries": False,
        "max_iterations": 40000,
    })
    assert report["solver_completed"] and report["first_party_translation_complete"]
    assert ["super_cases." + caller, "super_cases." + callee] in report["call_edges"]
    assert report["semantic_complete"] is False
    assert report["whole_program_soundness_proved"] is False
