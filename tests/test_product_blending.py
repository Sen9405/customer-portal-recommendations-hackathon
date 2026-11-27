from datetime import date
import sqlite3

from recommendation.profile_pipeline import ProfilePipeline
from recommendation.contest_recommender import ContestRecommender, _hash
from recommendation.early_popularity_experiment import combine


def fixture_rows():
    d = date(2026, 1, 1)
    rows = [('a', 'u', d, 'seen'), ('b', 'x', d, 'seen'), ('b', 'x', d, 'p1'),
            ('c', 'y', d, 'seen'), ('c', 'y', d, 'p2')]
    rows += [(str(i), 'one', d, 'repeat') for i in range(8)]
    rows += [('d', 'z', d, 'popular'), ('e', 'w', d, 'popular')]
    return d, rows


def test_blend_distinct_clients_and_preserve_original():
    d, rows = fixture_rows()
    p = ProfilePipeline(rows, d)
    before = p.profile('u')['candidates']
    mixed = p.blended_candidates('u')
    assert [x['item_id'] for x in mixed] == ['p1', 'p2', 'popular']
    assert mixed[2]['method'] == 'distinct_client_popularity'
    assert mixed[2]['evidence']['train_distinct_client_count'] == 2
    assert p.profile('u')['candidates'] == before
    for user in ('u', 'one', 'new'):
        profile = [c['item_id'] for c in p.profile(user)['candidates']]
        pop = [i for i in p.distinct_rank if i not in p.history.get(user, set())][:3]
        assert [c['item_id'] for c in p.blended_candidates(user)] == combine(profile, pop)
    assert [c['item_id'] for c in p.blended_candidates('u', eligible=lambda x: x != 'p1')] == ['p2', 'repeat', 'popular']
    assert p.blended_candidates('u', eligible=lambda x: False) == []


def test_eligibility_applied_before_personal_top_three():
    d = date(2026, 1, 1)
    rows = [('u0', 'u', d, 'seen')]
    for index in range(4):
        rows.extend([(f'o{index}', f'v{index}', d, 'seen'), (f'o{index}', f'v{index}', d, f'p{index}')])
    model = ProfilePipeline(rows, d)
    assert [c['item_id'] for c in model.profile('u')['candidates']] == ['p0', 'p1', 'p2']
    mixed = model.blended_candidates('u', eligible=lambda i: i not in {'p0','p1'})
    assert [x['item_id'] for x in mixed[:2]] == ['p2','p3']
    assert all(x['method'] == 'association' for x in mixed[:2])


def test_carousel_uses_blend_before_feed_and_keeps_tail(tmp_path):
    d, rows = fixture_rows()
    db, wb = tmp_path / 'feed.sqlite', tmp_path / 'book'
    wb.write_bytes(b'fixture')
    conn = sqlite3.connect(db)
    conn.executescript('create table metadata(key text,value text); create table offers(offer_id text,name text,vendor text,category_id text,url text,available text,params_json text);')
    for item in ('seen', 'p1', 'p2', 'popular', 'repeat', 'extra1', 'extra2'):
        conn.execute('insert into offers values (?,?,?,?,?,?,?)', (item, item, 'v', 'c', '', 'true', '[]'))
    conn.commit(); conn.close()
    mapping = {'provenance': {'workbook_sha256': _hash(wb), 'index_sha256': _hash(db)},
               'items': {i: {'candidates': [{'offer_id': i}]} for i in ('seen', 'p1', 'p2', 'popular', 'repeat')}}
    model = ContestRecommender(rows, d, mapping, db, workbook_path=wb)
    for segment in ('B2B', 'B2C', None):
        long = model.recommend('u', segment)['candidates']
        assert [c['item_id'] for c in long[:3]] == ['p1', 'p2', 'popular']
        assert long[2]['evidence']['method'] == 'distinct_client_popularity'
        assert len(long) == 5
        assert long[:3] == model.recommend('u', segment, limit=3)['candidates']
        assert len({c['offer_id'] for c in long}) == len(long)
    model.by_id['popular']['availability'] = 'false'
    assert [c['item_id'] for c in model.recommend('u')['candidates'][:3]] == ['p1', 'p2', 'repeat']
