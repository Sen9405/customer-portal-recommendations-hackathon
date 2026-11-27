import json
import sqlite3

import pytest

from recommendation.content_retrieval_audit import evaluate, load_items


def test_cold_retrieval_and_lexical_ablation():
    rows = [('1', 'u', '2026-01-01', 'a'),
            ('2', 'u', '2026-02-01', 'b')]
    items = {'a': {'category': 'c', 'name': 'product version one', 'text': 'shared license'},
             'b': {'category': 'c', 'name': 'product version one', 'text': 'shared license'},
             'z': {'category': 'different', 'name': 'other', 'text': 'shared license'}}
    r = evaluate(rows, items, '2026-01-01', '2026-02-01', None)['cohorts']['A']
    for model in ('category', 'tfidf'):
        assert r[model]['cold_targets'] == 1
        assert r[model]['cold_reachable'] == 1
        assert r[model]['cold_hits'] == 1
        assert r[model]['slots'] == 1
        assert r[model + '_lexical_ablation']['cold_reachable'] == 0
        assert r[model + '_lexical_ablation']['cold_hits'] == 0
    assert evaluate(rows, items, '2026-01-01', '2026-02-01', None)['cohorts']['B']['tfidf']['cold_hits'] == 1


def test_no_future_vocabulary_and_category_boundary():
    rows = [('1', 'u', '2026-01-01', 'a'), ('2', 'u', '2026-02-01', 'b')]
    items = {'a': {'category': 'c', 'name': '', 'text': 'trainword'},
             'b': {'category': 'c', 'name': '', 'text': 'futureword'},
             'x': {'category': 'other', 'name': '', 'text': 'trainword'}}
    r = evaluate(rows, items, '2026-01-01', '2026-02-01', None)['cohorts']['A']
    assert r['category']['cold_hits'] == 1
    assert r['tfidf']['cold_reachable'] == 0
    assert r['tfidf']['slots'] == 0
    with pytest.raises(ValueError):
        evaluate(rows, items, '2026-02-01', '2026-01-01', None)


def test_excludes_duplicate_offer_occurrences(tmp_path):
    path = tmp_path / 'index.sqlite'
    con = sqlite3.connect(path)
    con.execute('create table offers (offer_id text, name text, vendor text, params_json text)')
    con.executemany('insert into offers values (?,?,?,?)', [('1', 'one', 'vendor', '[]'), ('2', 'two', 'vendor', '[]'), ('2', 'two', 'vendor', '[]')])
    con.commit()
    con.close()
    snapshot = {'items': {x: {'candidate': {'offer_id': offer, 'category_id': 'cat', 'vendor': 'vendor'}} for x, offer in [('a', '1'), ('b', '2')]}}
    items = load_items(snapshot, str(path))
    assert set(items) == {'a'}
    assert 'one' in items['a']['text']
