"""Migration metadata must be complete and name real operational consumers."""
import ast
from dataclasses import fields
import json
from pathlib import Path
from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy

ROOT=Path(__file__).resolve().parents[3]


def test_control_catalog_covers_every_old_field_and_context():
    metadata=json.loads((ROOT/"pythonstan/config/control_metadata.json").read_text())
    baseline=json.loads((ROOT/"pythonstan/config/legacy_defaults.json").read_text())
    assert metadata["counts"]=={"native_legacy_fields":31,"registered_contexts":19,
                                "legacy_ai_budget_fields":25,"additional_solver_options":8}
    assert {row["old_name"] for row in metadata["native_options"]}==set(baseline["andersen"])
    assert {row["old_name"] for row in metadata["ai_budget_options"]}=={field.name for field in fields(AnalysisBudget)}
    assert {row["policy"] for row in metadata["context_policies"]}=={policy.value for policy in ContextPolicy}
    assert (ROOT/"docs/refactor/old_to_new_options.json").read_bytes()==(ROOT/"pythonstan/config/control_metadata.json").read_bytes()


def test_supported_consumer_symbols_are_defined_and_unsupported_are_honest():
    definitions=set()
    for path in (ROOT/"pythonstan").rglob("*.py"):
        module=".".join(path.relative_to(ROOT).with_suffix("").parts)
        class Visitor(ast.NodeVisitor):
            def __init__(self):self.stack=[]
            def visit_ClassDef(self,node):
                self.stack.append(node.name);definitions.add(".".join([module,*self.stack]));self.generic_visit(node);self.stack.pop()
            def visit_FunctionDef(self,node):
                self.stack.append(node.name);definitions.add(".".join([module,*self.stack]));self.generic_visit(node);self.stack.pop()
            visit_AsyncFunctionDef=visit_FunctionDef
        Visitor().visit(ast.parse(path.read_text()))
    metadata=json.loads((ROOT/"pythonstan/config/control_metadata.json").read_text())
    for group,key in (("native_options","actual_consumer"),("ai_budget_options","legacy_consumer"),
                      ("ai_budget_options","new_ai_consumer"),("new_options","consumer")):
        for row in metadata[group]:
            consumer=row.get(key)
            if consumer is not None:assert consumer in definitions,consumer
    budgets={row["old_name"]:row for row in metadata["ai_budget_options"]}
    assert budgets["max_recursion_depth"]["new_ai_status"]=="legacy_only_reject_nondefault"
    assert budgets["max_addr_set_size"]["new_ai_consumer"].endswith("InterproceduralDriver.coarsen_flows")
    assert budgets["max_addr_set_size"]["new_ai_status"]=="supported"
    assert budgets["max_const_ints"]["new_ai_consumer"] is None
    assert budgets["max_const_ints"]["legacy_status"]=="retired_reject_nondefault"
    assert budgets["max_const_strings"]["new_ai_consumer"].endswith("ValueOperations.__init__")
    assert {name for name,row in budgets.items() if row["legacy_status"]=="supported_shared_core"}=={
        "max_iterations","max_stmt_visits","max_addr_set_size","max_const_strings",
        "enable_narrowing","narrowing_iterations","enable_value_partition",
        "max_value_partitions","strict_exceptions"}
    for row in metadata["solver_options"]:
        assert all(consumer in definitions for consumer in row["actual_new_consumers"]+row["legacy_consumers"])
