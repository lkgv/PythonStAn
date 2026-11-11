"""Negative architecture examples, runnable with stdlib unittest or pytest."""
import contextlib
import importlib.util
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


def checker():
    path = Path(__file__).resolve().parents[3] / "scripts/refactor/check_architecture.py"
    spec = importlib.util.spec_from_file_location("pythonstan_architecture_checker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArchitectureContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = checker()

    def write(self, relative, source):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_engine_cross_import_and_delayed_optional_import_are_rejected(self):
        self.write("pythonstan/analysis/engines/abstract/driver.py", "def solve():\n    from ..andersen import solver\n    importlib.import_module('pythonstan_inspector.runner')\n")
        findings = self.module.inspect_root(self.root)
        rules = {row["rule"] for row in findings}
        self.assertIn("engines-bridge-only-through-composition", rules)
        self.assertIn("core-must-not-depend-on-optional-consumer", rules)

    def test_neutral_contracts_and_domains_reject_reverse_edges(self):
        self.write("pythonstan/analysis/contracts/results.py", "from ..domains import values\n")
        self.write("pythonstan/analysis/domains/values.py", "from ..engines.abstract import driver\n")
        rules = {row["rule"] for row in self.module.inspect_root(self.root)}
        self.assertIn("contracts-must-be-engine-neutral", rules)
        self.assertIn("domains-must-not-depend-on-engine", rules)

    def test_duplicate_closure_implementations_and_old_script_are_visible(self):
        self.write("pythonstan/frontend/closure.py", "class ClosureAnalysis: pass\n")
        self.write("pythonstan/analysis/scope/closure.py", "class ClosureAnalysis: pass\n")
        self.write("scripts/do_closure.py", "from pythonstan.analysis.scope import ClosureAnalysis\n")
        findings = self.module.inspect_root(self.root)
        duplicate = [row for row in findings if row["rule"] == "duplicate-closure-implementation"]
        self.assertEqual(len(duplicate), 2)
        self.assertTrue(any(row["rule"] == "duplicate-closure-source" for row in findings))

    def test_composition_is_allowed_and_checking_never_imports_target(self):
        self.write("pythonstan/analysis/composition/oracle.py", "from ..engines.abstract import driver\nfrom ..engines.andersen import solver\nraise RuntimeError('parse only')\n")
        self.assertEqual(self.module.inspect_root(self.root), [])

    def test_migration_debt_cannot_pass_release_gate(self):
        self.write("pythonstan/analysis/pointer/kcfa/solver.py", "from ..ai import api\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.module.main(["--root", str(self.root)]), 0)
            self.assertEqual(self.module.main(["--root", str(self.root), "--release"]), 1)
        self.assertIn('"mode": "release"', output.getvalue())

    def test_new_core_cannot_reverse_depend_on_compatibility_or_composition(self):
        self.write("pythonstan/analysis/engines/abstract/driver.py", "from pythonstan.compat import shared_ai\nfrom pythonstan.analysis.composition import state_adapter\n")
        rules = {row["rule"] for row in self.module.inspect_root(self.root)}
        self.assertIn("new-core-must-not-call-compat-shim", rules)
        self.assertIn("core-must-not-own-composition", rules)

    def test_current_inventory_keeps_frozen_targets_and_archive_is_not_a_delegate(self):
        import json
        spec = importlib.util.spec_from_file_location("module_inventory", Path(__file__).resolve().parents[3]/"scripts/refactor/update_module_inventory.py")
        inventory = importlib.util.module_from_spec(spec);spec.loader.exec_module(inventory)
        row={"old_module":"pythonstan.old","old_path":"pythonstan/old.py","new_module":"pythonstan.new"}
        record=inventory.record_for_row(row,self.root,archives={"pythonstan/old.py":{"path":"historical.py"}})
        self.assertEqual(record["status"],"historical_noninstalled_archive")
        self.assertIsNone(record["current_module"])
        self.assertFalse(record["supported_import"])
        missing=inventory.record_for_row(row,self.root,archives={})
        self.assertEqual(missing["status"],"removed_without_active_destination")
        self.assertIsNone(missing["current_module"])
        self.write("pythonstan/old.py","class OriginalImplementation: pass\n")
        self.write("pythonstan/new.py","class IntendedImplementation: pass\n")
        retained=inventory.record_for_row(row,self.root,archives={})
        self.assertEqual(retained["status"],"retained_at_original_location")
        self.assertEqual(retained["current_module"],"pythonstan.old")
        mapping=json.loads((Path(__file__).resolve().parents[3]/"docs/refactor/module_map.json").read_text())
        self.assertEqual(len(mapping["modules"]),150)

    def test_checker_self_test(self):
        self.module.self_test()


if __name__ == "__main__":
    unittest.main()
