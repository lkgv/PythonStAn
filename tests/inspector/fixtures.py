"""Frozen v1 DTO protocol fixtures, calibrated against the real P1 adapter.

These supplement source/engine tests; they are not adapter or Python oracles.
"""
from pythonstan_inspector.contract import SealedSnapshot, canonical_bytes
from pythonstan_inspector.storage import Bundle


def artifact(snapshot_id="s1", run_id="run1"):
    data = {"schema": {"major": 1, "minor": 0},
        "run": {"run_id": run_id, "stop_reason": "iteration_limit", "implementation_sha256": "fixed-code",
            "inputs": [{"path": "subject.py", "sha256_before": "fixed-source", "sha256_after": "fixed-source"}],
            "configuration": {"context_policy": "0-cfa"}, "entry_points": ["subject.py"],
            "dependency_policy": "imports_disabled", "models": "fixed-models", "cache": "new_world"},
        "snapshot": {"run_id": run_id, "snapshot_id": snapshot_id,
            "boundary": {"coherent": True, "stage": "stopped"}, "scope": "all_current_state",
            "entities": [
                {"id": 7, "kind": "node", "label": "node:x", "origin": {"file": "subject.py", "line": 1},
                 "attributes": {"node_kind": "normal", "engine_class": "NormalNode", "name": "x"}, "links": {}},
                {"id": 8, "kind": "node", "label": "node:y", "origin": {"file": "subject.py", "line": 2},
                 "attributes": {"node_kind": "normal", "engine_class": "NormalNode", "name": "y"}, "links": {}},
                {"id": 9, "kind": "object", "label": "payload", "origin": {"file": "subject.py", "line": 3},
                 "attributes": {"engine_class": "ConstantObject", "value": 1, "native_summary": False}, "links": {}}],
            "views": {
                "node_points": {"version": 1, "fidelity": "exact", "dimensions": ["owner", "object", "lane"],
                    "operations": ["enumerate", "contains"], "coverage": {"registry": "complete", "owners": {
                        "7": {"fields": {"facts": "complete"}}, "8": {"fields": {"facts": "complete"}}}},
                    "rows": [{"owner": 7, "object": 9, "lane": "objects"},
                             {"owner": 8, "object": 9, "lane": "objects"}]},
                "pfg_edges": {"version": 1, "fidelity": "exact", "dimensions": ["source", "target", "edge_kind"],
                    "operations": ["enumerate"], "coverage": {"registry": "complete", "owners": {}},
                    "rows": [{"source": 7, "target": 8, "edge_kind": "normal", "identity_copy": True}]}},
            "stats": {"iterations": 4, "capture_seconds": 0.001}, "omissions": []}}
    entities = data["snapshot"]["entities"]
    entities[0]["links"]["variable"] = {"ref": 11, "status": "resolved"}
    entities[1]["links"]["variable"] = {"ref": 12, "status": "resolved"}
    entities[2]["links"].update(context={"ref": 13, "status": "resolved"}, allocation={"ref": 15, "status": "resolved"})
    entities.extend([
        {"id": 11, "kind": "ctx", "label": "x", "origin": {"file": "subject.py", "line": 1},
         "attributes": {"name": "x"}, "links": {"context": {"ref": 13, "status": "resolved"}, "scope": {"ref": 14, "status": "resolved"}}},
        {"id": 12, "kind": "ctx", "label": "y", "origin": {"file": "subject.py", "line": 2},
         "attributes": {"name": "y"}, "links": {"context": {"ref": 13, "status": "resolved"}, "scope": {"ref": 14, "status": "resolved"}}},
        {"id": 13, "kind": "context", "label": "context", "origin": {},
         "attributes": {"k": 0, "call_sites_length": 0}, "links": {}},
        {"id": 14, "kind": "scope", "label": "module", "origin": {"file": "subject.py"},
         "attributes": {}, "links": {"definition": {"ref": 16, "status": "resolved"}, "context": {"ref": 13, "status": "resolved"}}},
        {"id": 15, "kind": "origin", "label": "allocation", "origin": {"file": "subject.py", "line": 3},
         "attributes": {}, "links": {}},
        {"id": 16, "kind": "origin", "label": "module", "origin": {"file": "subject.py"},
         "attributes": {}, "links": {}}])
    for entity in entities:
        # Affirmatively calibrated, unclipped fixture descriptions. Legacy
        # producers with no marker retain local membership, not alignment.
        entity["attributes"]["description_coverage"] = "complete"
    return data


def bundle(data=None, trace=(), health=None):
    return Bundle(SealedSnapshot.seal(data or artifact()), canonical_bytes({}),
                  canonical_bytes(list(trace)), canonical_bytes(health or {"status": "complete", "integrity": "matched"}))
