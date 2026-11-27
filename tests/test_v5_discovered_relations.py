import json
from types import SimpleNamespace
from unittest import TestCase

from recommendation.v5_discovered_relations import NANO, matches, select_and_score


def test_nano_relation_requires_release_vendor_and_os_match():
    source = {'vendor': NANO, 'name': 'Нанософт Платформа nanoCAD 26 (год)'}
    dest = {'vendor': NANO, 'name': 'Нанософт Платформа nanoCAD 26 (конфигурация Standart Mech)'}
    assert matches('nanocad-mech', source, dest)
    assert not matches('nanocad-build', source, dest)
    assert not matches('nanocad-mech', {**source, 'name': source['name'].replace('26','25')}, dest)
    assert not matches('nanocad-mech', {**source, 'name': source['name'] + ' Linux'}, dest)
    assert not matches('nanocad-mech', {**source, 'name': source['name'] + ' (конфигурация Pro)'}, dest)
    assert not matches('nanocad-mech', source, {**dest, 'vendor': 'other'})


def test_model_selection_never_receives_ids_or_labels_and_can_abstain():
    base = ['private-i1', 'private-i2', 'private-i3']
    case = (('A', 'B'), base, {'private-truth'}, {'private-truth': ['pdf16-business']})
    rec = SimpleNamespace(_unique=lambda _: {'name': 'Public edition'})
    totals = {c: {'profile_hits': 7} for c in ('A','B')}

    def invoke(_, payload):
        assert 'private-' not in payload
        assert json.loads(payload) == {'p1': {'c1': {'offer':'Public edition', 'relations':['pdf16-business']}}}
        return '{"p1":"c1"}', None

    assert select_and_score([case],rec,invoke,totals)['cohorts']['A'] == dict(changed=1,added=1,lost=0,hits=8)
    abstention = select_and_score([case],rec,lambda *_: ('{"p1":null}',None),totals)
    assert abstention['cohorts']['A']['hits'] == 7
    with TestCase().assertRaises(ValueError):
        select_and_score([case],rec,lambda *_: ('{"p1":"c2"}',None),totals)
