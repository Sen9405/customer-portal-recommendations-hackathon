from recommendation.cold_rank_audit import audit, rank_scores


def test_rank_variants_ablation_and_ties():
    edges = [('a', 'x', .4, False), ('b', 'x', .4, True),
             ('a', 'y', .6, False), ('a', 'z', .6, False)]
    assert rank_scores(edges, {'a', 'b'}, 'sum')[0] == ['x', 'y', 'z']
    assert rank_scores(edges, {'a', 'b'}, 'max')[0] == ['y', 'z', 'x']
    assert rank_scores(edges, {'a', 'b'}, 'mean')[0] == ['y', 'z', 'x']
    assert rank_scores(edges, {'a', 'b'}, 'sum', True)[0] == ['y', 'z', 'x']
    assert rank_scores(edges, {'a', 'b'}, 'sum', exposed={'y', 'z'})[0] == ['x']
    assert 'x' not in rank_scores(edges, {'a', 'b', 'x'})[0]


def test_audit_reachable_and_rank_counts():
    rows = [('o', 'u', '2026-01-01', 'a'), ('p', 'v', '2026-01-01', 'b'),
            ('q', 'u', '2026-02-01', 'c')]
    items = {x: {'name': name, 'text': text, 'category': category} for x, name, text, category in [
        ('a', 'alpha source', 'alpha beta', 'one'), ('b', 'beta source', 'beta', 'two'),
        ('c', 'alpha target', 'alpha', 'two')]}
    summary, private = audit(rows, items, ('2026-01-01', '2026-02-01', '2026-02-28'))
    assert summary['users'] == 1
    assert summary['variants']['sum']['cold_targets'] == 1
    assert summary['variants']['sum']['reachable'] == 1
    assert summary['variants']['sum']['cold_hits'] == 1
    assert sum(summary['base_reachable_rank_buckets'].values()) == len(private) == 1
    assert private[0]['rank'] >= 1
