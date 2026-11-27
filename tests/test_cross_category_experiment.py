import sqlite3
import pytest
from recommendation.cross_category_experiment import eligible_items, evaluate


def test_cross_category_ablation_and_rank():
    rows = [('o', 'u', '2026-01-01', 'a'), ('p', 'u', '2026-02-01', 'b')]
    items = {'a': {'category': 'one', 'name': 'same product version', 'text': 'common license'},
             'b': {'category': 'two', 'name': 'same product version', 'text': 'common license'},
             'c': {'category': 'one', 'name': 'other', 'text': 'unrelated'}}
    result = evaluate(rows, items, '2026-01-01', '2026-02-01', '2026-02-01')['cohorts']
    a = result['A']
    assert a['category']['cold_targets'] == 1
    assert a['category']['cold_eligible'] == 1
    assert a['category']['cold_reachable'] == 0
    assert a['gated_tfidf']['cold_reachable'] == 0
    assert a['global_tfidf']['cold_reachable'] == 1
    assert a['global_tfidf']['cold_reachable_cross_category_only'] == 1
    assert a['global_tfidf']['cold_hits'] == 1
    assert a['global_tfidf_lexical_ablation']['cold_reachable'] == 0
    assert result['B']['global_tfidf']['cold_hits'] == 1
    with pytest.raises(ValueError):
        evaluate(rows, items, '2026-02-01', '2026-01-01', None)


def test_train_vocabulary_empty_and_unseen_words():
    rows = [('o', 'u', '2026-01-01', 'a'), ('p', 'u', '2026-02-01', 'b')]
    items = {'a': {'category': '', 'name': '', 'text': 'trainword'},
             'b': {'category': '', 'name': '', 'text': 'futureword'}}
    result = evaluate(rows, items, '2026-01-01', '2026-02-01', None)['cohorts']['A']
    assert result['global_tfidf']['cold_reachable'] == 0
    items['a']['text'] = ''
    assert evaluate(rows, items, '2026-01-01', '2026-02-01', None)['cohorts']['A']['global_tfidf']['slots'] == 0


def test_same_universe_excludes_unmatched_ambiguous_and_duplicates(tmp_path):
    db = tmp_path / 'index.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('create table offers (offer_id text, name text, vendor text, params_json text)')
        con.executemany('insert into offers values (?, ?, ?, ?)', [(str(i), 'name', 'vendor', '[]') for i in (1, 2, 3, 4, 4, 5)])
    pairs = [('a', '1'), ('b', '2'), ('c', '3'), ('d', '4'), ('e', '5'), ('f', '5')]
    snapshot = {'items': {i: {'candidate': {'offer_id': o, 'category_id': 'x', 'vendor': 'v'}} for i, o in pairs}}
    audit = {'items': {i: {'matches': {'all_union': matches}} for i, matches in [('a', [1]), ('b', []), ('c', [1, 2]), ('d', [1]), ('e', [1]), ('f', [1])]}}
    assert set(eligible_items(snapshot, audit, str(db))) == {'a'}
