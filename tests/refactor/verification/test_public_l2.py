"""Public L2 authority stays scoped to explicitly requested properties."""
from pythonstan import Source, Project, PropertyQuery, ValueQuery, analyze


SOURCE = """def identity(payload):
    return payload
def box(payload):
    return {'value': payload}
def dispatch(table, key, payload):
    return table[key](payload)
table = {'read': identity, 'write': box}
plain = dispatch(table, 'read', 'original')
boxed = dispatch(table, 'write', 'retained')
"""
PROPERTY = "guarded_dispatch_return_relation"


def config(**changes):
    options = {"engine": "ai_primary", "resources": {"max_work": 100000, "max_seconds": 5},
               "llm": {"enabled": True, "level2": True, "provider": "offline"}}
    for name, value in changes.items():
        if isinstance(value, dict):
            options.setdefault(name, {}).update(value)
        else:
            options[name] = value
    return options


def test_internal_certificate_and_proof_view_do_not_promote_arbitrary_value_query():
    query = ValueQuery("example", "plain")
    result = analyze(Source(SOURCE, module="example"), config=config(), queries=[query, "proof"])
    assert result.solver_status == "converged"
    assert result.proof_status == "not_requested"
    assert result.query(query).proof_status == "not_requested"
    assert result.query(query).value["constants"] == ["original"]
    assert result.raw_snapshot()["llm"]["level2"]["applications"] == 2


def test_explicit_relation_property_is_verified_without_changing_solver_status():
    query = PropertyQuery(PROPERTY)
    result = analyze(Source(SOURCE, module="example"), config=config(), queries=[query])
    assert result.solver_status == "converged"
    assert result.proof_status == "verified"
    assert result.query(query).proof_status == "verified"
    records = result.query(query).value["records"]
    assert len(records) == 2
    assert all(row["status"] == "verified" for row in records)


def test_all_module_scope_requires_unproposed_modules_but_point_scope_is_independent(tmp_path):
    (tmp_path / "example.py").write_text(SOURCE)
    (tmp_path / "unrelated.py").write_text("unrelated = 1\n")
    target = Project(tmp_path)
    all_query = PropertyQuery(PROPERTY)
    result = analyze(target, config=config(), queries=[all_query])
    assert result.proof_status == "unknown"
    records = result.query(all_query).value["records"]
    assert any(row["module"] == "unrelated" and row["status"] == "unknown" for row in records)
    point = next(row["point"] for row in records if row["status"] == "verified")
    point_query = PropertyQuery(PROPERTY, point=point)
    selected = analyze(target, config=config(), queries=[point_query])
    assert selected.query(point_query).proof_status == "verified"
    assert all(row["point"] == point for row in selected.query(point_query).value["records"])
    both = analyze(target, config=config(), queries=[all_query, point_query])
    assert both.proof_status == "unknown"
    assert both.query(all_query).proof_status == "unknown"
    assert both.query(point_query).proof_status == "verified"


def test_malicious_candidate_falls_back_and_budget_stop_stays_a_budget_stop():
    query = PropertyQuery(PROPERTY)
    rejected = analyze(Source(SOURCE, module="example"), config=config(llm={"provider": "adversarial"}),
                       queries=[query, "values"])
    assert rejected.solver_status == "converged"
    assert rejected.query(ValueQuery("example", "plain")).value["constants"] == ["original"]
    assert rejected.query(query).proof_status == "unknown"
    exhausted = analyze(Source(SOURCE, module="example"), config=config(resources={"max_work": 1}),
                        queries=[query])
    assert exhausted.solver_status == "budget_exhausted"
    assert exhausted.proof_status == "unknown"
    assert exhausted.query(query).proof_status == "unknown"
