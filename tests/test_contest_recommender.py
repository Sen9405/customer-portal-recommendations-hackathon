import json
import sqlite3
from datetime import date

import pytest

from recommendation.contest_recommender import ContestRecommender, _audience, _hash, historical_profile


@pytest.fixture
def setup_catalog(tmp_path):
    db = tmp_path / 'feed.sqlite'
    workbook = tmp_path / 'book.xlsx'
    workbook.write_bytes(b'fixture')
    conn = sqlite3.connect(db)
    conn.executescript('create table metadata(key text,value text); create table offers(offer_id text,name text,vendor text,category_id text,url text,available text,params_json text);')
    conn.execute("insert into metadata values('feed_date_as_supplied','2026-11-26')")
    def add(oid, vendor, cat, availability='true', params='[]'):
        conn.execute('insert into offers values (?,?,?,?,?,?,?)', (oid, oid, vendor, cat, '', availability, params))
    add('a', 'v', 'c'); add('b', 'v', 'c'); add('cold', 'v', 'c', params=json.dumps([{'name':'Пользователь','value':'Физлицо'}])); add('bad', 'v', 'c', 'false'); add('unknown', 'v', 'c', None)
    add('dup','v','c'); add('dup','v','c')
    add('bought_ambiguous','v','c'); add('other','v','c')
    conn.commit(); conn.close()
    def c(oid): return {'offer_id': oid, 'duplicate_feed_offer_id': False}
    mapping = {'provenance': {'workbook_sha256': _hash(workbook), 'index_sha256': _hash(db)}, 'items': {
        'a': {'candidates':[c('a')]}, 'b': {'candidates':[c('b')]},
        'bought': {'candidates':[c('bought_ambiguous'),c('other')]}}}
    rows = [('1','u',date(2026,1,1),'a'),('2','u',date(2026,1,1),'bought'),('3','v',date(2026,1,1),'a'),('3','v',date(2026,1,1),'b')]
    return rows, mapping, db, workbook


def test_demo_snapshot_no_repeat_and_cold(setup_catalog):
    rows, m, db, wb = setup_catalog
    model = ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)
    result = model.recommend('u', 'B2C')
    offers = {r['offer_id'] for r in result['candidates']}
    assert offers == {'b', 'cold'}
    assert {r['source'] for r in result['candidates']} == {'profile','related_feed'}
    assert all(r['snapshot_available'] is True for r in result['candidates'])
    assert next(r for r in result['candidates'] if r['offer_id'] == 'cold')['audience_hint'] == 'matching_hint'
    assert result['snapshot_date'] == '2026-11-26'


def test_carousel_can_exceed_three_but_first_three_stable(setup_catalog):
    rows, m, db, wb = setup_catalog
    model = ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)
    for i in range(8):
        oid='extra'+str(i)
        obj={'offer_id':oid, 'name':oid, 'vendor':'v', 'category_id':'c', 'url':'', 'availability':'true', 'params':'[]'}
        model.by_category['c'].append(obj)
    short=model.recommend('u','B2B',limit=3)['candidates']
    long=model.recommend('u','B2B',limit=10)['candidates']
    assert len(long)>3
    assert long[:3]==short
    assert len({c['offer_id'] for c in long})==len(long)
    with pytest.raises(ValueError): model.recommend('u',limit=0)


def test_unknown_soft_and_mixed():
    def p(*v): return json.dumps([{'name':'Пользователь','value':x} for x in v])
    assert _audience('garbage', 'B2B') == ('unknown', 0)
    assert _audience(p('не определено'), 'B2B') == ('unknown', 0)
    assert _audience(p('Юрлицо','Физлицо'), 'B2B') == ('mixed', 0)
    assert _audience(p('Физлицо'), 'B2B') == ('different_hint', -1)
    assert _audience(p('Юрлицо'), None) == ('unknown', 0)


def test_future_rejected_and_historical_unaffected(setup_catalog):
    rows, m, db, wb = setup_catalog
    before = historical_profile(rows, '2026-01-01', 'u')
    with pytest.raises(ValueError): historical_profile(rows + [('4','u',date(2026,1,8),'z')], '2026-01-01', 'u')
    with pytest.raises(ValueError): ContestRecommender(rows + [('4','u',date(2026,1,8),'z')], '2026-01-01', m, db, workbook_path=wb)
    assert before == historical_profile(rows, '2026-01-01', 'u')
    m['provenance']['index_sha256'] = 'bad'
    with pytest.raises(ValueError): ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)


def test_no_history_or_unmapped_history_no_unrelated_feed(setup_catalog):
    rows, m, db, wb = setup_catalog
    model = ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)
    assert all(c['source'] == 'profile' for c in model.recommend('new')['candidates'])
    with pytest.raises(ValueError): model.recommend('u', 'other')


def test_shared_mapping_never_certain(setup_catalog):
    rows, m, db, wb = setup_catalog
    m['items']['x'] = {'candidates':[{'offer_id':'a'}]}
    model = ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)
    assert model._unique('a') is None
    assert all(c['source'] != 'related_feed' for c in model.recommend('u')['candidates'])


def test_same_pool_includes_eligible_train_and_related_beyond_carousel(setup_catalog):
    rows, m, db, wb = setup_catalog
    for i in range(5):
        m['items'][f'train{i}'] = {'candidates': [{'offer_id': f'train{i}'}]}
        rows.append((f'order{i}', 'v', date(2026, 1, 1), f'train{i}'))
    model = ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)

    def offer(oid, params='[]'):
        return dict(model.by_id['b'], offer_id=oid, name=oid, params=params)

    # No need to share category/vendor to qualify through the train candidate path.
    for i in range(5):
        model.by_id[f'train{i}'] = dict(offer(f'train{i}'), category_id='different', vendor='different')
    for i in range(24):
        model.by_category['c'].append(offer(f'extra{i:02}'))
    model.by_category['c'].append(offer('renewal', '[{"name":"Тип лицензии","value":"Продление"}]'))
    demo, pool = model.recommend_with_catalog_pool('u', 'B2B')
    assert demo == model.recommend('u', 'B2B')
    assert pool == {'b': 'b', **{f'train{i}': f'train{i}' for i in range(5)},
                    **{f'extra{i:02}': None for i in range(24)}}
    assert len(demo['candidates']) == 20
    assert {c['offer_id'] for c in demo['candidates']} < pool.keys()
    with pytest.raises(ValueError, match='observed history'):
        model.recommend_with_catalog_pool('new', 'B2B')


def test_same_pool_applies_platform_policy_to_both_sources(setup_catalog):
    rows, m, db, wb = setup_catalog
    model = ContestRecommender(rows, '2026-01-01', m, db, workbook_path=wb)
    model.by_id['b']['params'] = '[{"name":"Совместимая архитектура/платформа","value":"x86-64"}]'
    model.by_category['c'].append(dict(model.by_id['b'], offer_id='arm-feed', name='arm-feed',
        params='[{"name":"Совместимая архитектура/платформа","value":"ARMv8"}]'))
    options = {'platform': 'armv8', 'require_platform': True}
    demo, pool = model.recommend_with_catalog_pool('u', 'B2C', **options)
    assert pool == {'arm-feed': None}
    assert demo == model.recommend('u', 'B2C', **options)
