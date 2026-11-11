"""Passive observation and detached causal explanation acceptance."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from pythonstan.analysis.contracts import SourceModule, EntryScenario
from pythonstan.analysis.contracts.observation import ObservationSink, CausalEvent
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan_inspector.refactor_adapter import FrozenResultInspector

CODE = """def ident(payload):
    return payload

def outer():
    value = 'after'
    return ident(value)

result = outer()
"""


def run(observer=None):
    source = SourceModule('example', 'example.py', CODE)
    driver = InterproceduralDriver((source,), options={
        'resources': {'max_work': 20000, 'max_seconds': 0},
        'abstract': {'max_traces_per_block': 8}}, observer=observer)
    snapshot = driver.run((EntryScenario('example'),)).to_dict()
    return snapshot


def semantics(snapshot):
    return {key: value for key, value in snapshot.items() if key not in ('stats', 'phase_trace')}


def test_retained_events_are_immutable_detached_and_versioned():
    sink = ObservationSink(run_id='test', max_events=3)
    event = {'kind': 'transfer', 'unit': 'unit:one', 'point': 'source-point',
             'input': {'constants': ['before']}}
    assert sink.emit(event)
    event['input']['constants'].append('mutation')
    assert sink.records[0].payload['input']['constants'] == ('before',)
    with pytest.raises((TypeError, FrozenInstanceError)):
        sink.records[0].payload['input']['constants'] += ('change',)
    snapshot = sink.snapshot()
    snapshot['events'][0]['payload']['input']['constants'].append('local')
    assert sink.snapshot()['events'][0]['payload']['input']['constants'] == ['before']
    assert sink.records[0].schema_version == 1
    assert isinstance(sink.records[0], CausalEvent)


def test_limits_and_invalid_payload_only_change_observation_status():
    sink = ObservationSink(max_events=2, max_payload_nodes=8, max_string=16)
    assert sink.emit({'kind': 'transfer', 'point': 'p', 'values': list(range(100000))})
    assert not sink.emit({'kind': 'bad', 'value': object()})
    assert sink.emit({'kind': 'finish'})
    assert not sink.emit({'kind': 'after-limit'})
    result = sink.snapshot()
    assert result['retained_events'] == 2
    assert result['total_events'] == 4
    assert result['errors'] == 1
    assert result['dropped_events'] == 1
    assert result['truncated'] and result['observation_status'] == 'partial'
    assert result['retained_bytes'] <= result['limits']['max_bytes']
    empty = ObservationSink(max_events=0)
    for i in range(100):
        empty.emit({'kind': 'work_dequeue', 'unit': 'unit:' + str(i), 'revision': i})
    assert empty.snapshot()['retained_events'] == 0
    assert not empty._unit_revision and not empty._dependencies


def test_observer_off_on_truncated_and_faulty_preserve_semantics_and_work():
    plain = run()
    full = ObservationSink(run_id='full', sources=(SourceModule('example', 'example.py', CODE),))
    tiny = ObservationSink(run_id='tiny', max_events=3)
    class Faulty:
        def emit(self, event):
            raise RuntimeError('observer failure')
    observed, truncated, faulty = run(full), run(tiny), run(Faulty())
    for variant in (observed, truncated, faulty):
        assert semantics(variant) == semantics(plain)
        assert variant['stats']['work'] == plain['stats']['work']
        assert variant['obligations'] == plain['obligations']
    assert full.snapshot()['retained_events'] > 3
    assert tiny.snapshot()['truncated']
    assert faulty['stats']['observation_errors'] > 0
    assert full.stats['capture_seconds'] > 0


def test_query_source_inputs_transfer_summaries_dependencies_projection_chain():
    source = SourceModule('example', 'example.py', CODE)
    sink = ObservationSink(run_id='causal', sources=(source,))
    snapshot = run(sink)
    snapshot['events'] = sink.snapshot()
    inspector = FrozenResultInspector(snapshot)
    explanation = inspector.explain(module='example', symbol='result')
    assert explanation['status'] == 'observed_chain'
    assert explanation['projection']['value']['constants'] == ['after']
    assert explanation['sources'] and any(source['excerpt'] for source in explanation['sources'])
    assert any(event['payload'].get('value', {}).get('constants') == ['after']
               for event in explanation['inputs'])
    assert explanation['summaries']
    assert explanation['dependencies']
    kinds = {event['kind'] for event in explanation['chain']}
    assert {'transfer', 'name_read', 'name_write', 'summary_update', 'call_dependency'} <= kinds
    assert not explanation['truncated']
    explanation['projection']['value']['constants'].append('mutated')
    assert inspector.explain(module='example', symbol='result')['projection']['value']['constants'] == ['after']
    json.dumps(explanation)


def test_corrupted_projection_can_be_traced_without_repairing_facts():
    # A controlled exported-answer fault demonstrates diagnosis, not a claim of
    # finding a new analyzer bug. Trace cannot silently repair the stored answer.
    sink = ObservationSink(run_id='projection-fault', sources=(SourceModule('example', 'example.py', CODE),))
    snapshot = run(sink)
    snapshot['events'] = sink.snapshot()
    faulty = deepcopy(snapshot)
    faulty['values']['example']['result']['constants'] = ['wrong']
    inspector = FrozenResultInspector(faulty)
    explanation = inspector.explain(module='example', symbol='result')
    assert explanation['projection']['value']['constants'] == ['wrong']
    writes = [event for event in explanation['chain'] if event['kind'] == 'name_write'
              and event['payload'].get('cell') == 'global:example:result']
    assert any(event['payload']['value']['constants'] == ['after'] for event in writes)
    assert inspector.snapshot()['values']['example']['result']['constants'] == ['wrong']
    assert explanation['claim'] == 'diagnostic_observation_only'


def test_unknown_producer_schema_is_partial_not_silently_reinterpreted():
    sink = ObservationSink()
    assert not sink.emit({'schema_version': 2, 'kind': 'name_write', 'value': 'foreign'})
    assert not sink.emit({'schema_version': True, 'kind': 'name_write'})
    snapshot = sink.snapshot()
    assert snapshot['errors'] == 2 and not snapshot['events']
    assert snapshot['observation_status'] == 'partial'
