from datetime import date

from evaluation.protocol_audit import analyze, fit_official, recommend_official


def test_official_baskets_collisions_duplicates_and_distinct_customers():
    train = [
        ('shared', 'u1', date(2026, 8, 24), 'a'),
        ('shared', 'u2', date(2026, 8, 24), 'b'),
        ('shared', 'u1', date(2026, 8, 24), 'a'),
        ('shared', 'u1', date(2026, 8, 31), 'c'),
        ('shared', 'u1', date(2026, 8, 31), 'a'),
        ('different', 'u1', date(2026, 8, 31), 'b'),
    ]
    model = fit_official(train)
    assert model['pairs']['a']['b'] == 1  # cross-client same order/week
    assert model['pairs']['a']['c'] == 1  # reused order, separate week
    assert model['pairs']['b']['c'] == 0  # no cross-week join
    assert model['counts']['a'] == 1  # duplicate rows and orders, same client
    assert model['counts']['b'] == 2
    assert recommend_official(model, {'a'}, 'copurchase') == ['b', 'c']


def test_ties_popularity_then_id_and_fixed_denominator():
    train = [('s', 'other', date(2026, 8, 31), i) for i in ('a', 'b', 'c', 'd', 'e')]
    train += [('u-order', 'u', date(2026, 8, 31), 'a'),
              ('extra', 'third', date(2026, 8, 31), 'e')]
    model = fit_official(train)
    assert recommend_official(model, {'a'}, 'copurchase') == ['e', 'b', 'c']
    rows = train + [('future', 'u', date(2026, 9, 1), 'e')]
    result = analyze(rows, {'u': 'B2B'})
    assert result['rows']['test'] == 1
    assert result['metrics']['copurchase']['A']['precision_at_3'] == 1 / 3


def test_short_and_empty_lists_are_misses_and_fill_is_nonempty():
    rows = [('1', 'u', '2026-08-31', 'a'),
            ('1', 'other', '2026-08-31', 'b'),
            ('2', 'v', '2026-08-31', 'z'),
            ('3', 'u', '2026-09-07', 'b'),
            ('4', 'v', '2026-09-07', 'b')]
    r = analyze(rows, {'u': 'B2B', 'v': 'B2C'})
    assert r['metrics']['copurchase']['A']['short_lists'] == 2
    assert r['metrics']['copurchase']['A']['fill_rate'] == 0.5
    assert r['metrics']['popularity']['B']['precision_at_3'] == 1 / 3
