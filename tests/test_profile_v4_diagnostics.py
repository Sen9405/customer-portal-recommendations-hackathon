from datetime import date
from recommendation.profile_v4_diagnostics import relation, audit, rank
from recommendation.profile_pipeline import ProfilePipeline


def test_relation_unknown_and_proxy():
    assert relation('abcdefghij', 'abcdefghiX') == 'similar'
    assert relation('', 'abcdefghij') == 'unknown'
    assert relation('abcdefghij', 'zyxwvutsrq') == 'other'


def test_ranking_loo_and_filters():
    rows = [('o1','u','2026-01-05','a'), ('o1','u','2026-01-05','b'),
            ('o2','u','2026-01-12','c'), ('o3','v','2026-01-05','a'),
            ('o3','v','2026-01-05','d')]
    p = ProfilePipeline(rows, date(2026,1,12))
    assert rank(p,'u') == [x['item_id'] for x in p.profile('u')['candidates']]
    assert rank(p,'u', own=ProfilePipeline(rows[:3], date(2026,1,12))) == ['d']
    assert rank(p,'u', blocked={'d'}) == []


def test_temporal_train_only_names_and_counts():
    rows = [('o1','u','2026-01-05','a','Alpha product'),
            ('o2','v','2026-01-05','b','Beta product!'),
            ('o3','u','2026-01-12','b','Alpha product'),
            ('o4','u','2026-01-12','c','Alpha product')]
    result = audit(rows, '2026-01-05','2026-01-12','2026-01-12')
    a = result['A']
    assert a['clients'] == 1 and a['denominator'] == 3
    assert a['profile']['hits_unknown'] == 0
    assert a['profile']['hits_other'] == 1
    assert a['unseen_train_target_count'] == 1


def test_edge_ablation_and_candidate_filter_are_separate():
    rows = [('1','u','2026-01-05','a'),
            ('2','v','2026-01-05','a'), ('3','v','2026-01-12','b'),
            ('4','w','2026-01-05','b')]
    p = ProfilePipeline(rows, date(2026,1,12))
    assert rank(p, 'u')[0] == 'b'
    # Removing the edge still permits the same item via fallback.
    assert rank(p, 'u', edge_ok=lambda a, b: False)[0] == 'b'
    assert rank(p, 'u', candidate_ok=lambda item: item != 'b') == []


def test_loo_subtracts_both_graphs_without_dropping_history():
    rows = [('1','u','2026-01-05','a'), ('1','u','2026-01-05','b'),
            ('2','u','2026-01-12','c'), ('3','v','2026-01-05','a'),
            ('3','v','2026-01-05','d')]
    p = ProfilePipeline(rows, date(2026,1,12))
    own = ProfilePipeline(rows[:3], date(2026,1,12))
    assert p.co['a']['b'] - own.co['a']['b'] == 0
    assert p.seq['a']['c'] - own.seq['a']['c'] == 0
    assert not set(rank(p, 'u', own=own, remove_own_pop=True)) & p.history['u']
