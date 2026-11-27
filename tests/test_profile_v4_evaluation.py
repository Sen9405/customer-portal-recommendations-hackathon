from recommendation.profile_v4_evaluation import evaluate, official_models, profile_rank, split_rows
from recommendation.profile_pipeline import ProfilePipeline


def test_cutoff_dedup_and_official_baskets():
    rows = [('o','u','2026-08-31','a'), ('o','u','2026-08-31','a'),
            ('o','v','2026-08-31','b'), ('o','v','2026-09-07','future'),
            ('o','v','2026-08-24','c')]
    train, hist, future = split_rows(rows)
    pop, co = official_models(train, hist)
    assert len(train) == 4 and future['v'] == {'future'}
    assert pop['a'] == 1 and co['a']['b'] == 1 and co['a']['c'] == 0
    p = ProfilePipeline(rows, '2026-08-31')
    assert 'future' not in profile_rank(p, 'u', 'full')
    assert profile_rank(p, 'u', 'full') == [x['item_id'] for x in p.profile('u')['candidates']]


def test_ablation_weights_temporal_and_frequency():
    links = [('x','v','2026-01-01','a'), ('x','v','2026-01-01','x'),
             ('y','w','2026-01-01','b'), ('y','w','2026-01-01','y')]
    history = [('1','u','2026-01-01','a'), ('2','u','2026-03-01','b'),
               ('3','u','2026-03-01','b')]
    p = ProfilePipeline(history + links, '2026-04-01')
    assert profile_rank(p, 'u', 'full')[0] == 'y'
    assert profile_rank(p, 'u', 'no_recency')[0] == 'y'
    assert profile_rank(p, 'u', 'no_frequency')[0] == 'y'
    assert profile_rank(p, 'u', 'neither')[0] == 'x'
    assert profile_rank(p, 'u', 'full') == [x['item_id'] for x in p.profile('u')['candidates']]


def test_score_short_lists_duplicate_and_empty():
    r = evaluate(['u', 'v'], {'u': {'a', 'b'}, 'v': {'z'}},
                 lambda u: ['a', 'a', 'b'] if u == 'u' else [])
    assert r == {'clients': 2, 'matches': 2, 'denominator': 6,
                 'precision_at_3': 2/6, 'nonempty': 1, 'full_triples': 0}
    assert evaluate([], {}, lambda u: [])['precision_at_3'] is None
