from datetime import date
from types import SimpleNamespace

from recommendation.contest_v5_evaluation import demo_metrics, evaluate, popularity_control


def test_unresolved_are_not_assumed_misses_and_extra_carousel_ignored():
    recs = {'u': [{'item_id': 'b'}, {'item_id': None}, {'item_id': None}, {'item_id': 'c'}]}
    metric = demo_metrics(['u'], {'u': {'b', 'c'}}, {'u': {'a'}},
                          {'a': 'g', 'b': 'g', 'c': 'h'}, recs)
    assert metric['known_hits'] == 1
    assert metric['precision_at_3_lower_bound'] == 1/3
    assert metric['precision_at_3_upper_bound_loose'] == 2/3
    assert (metric['known_hit_variants'], metric['known_hit_new']) == (1, 0)
    assert (metric['returned_slots'], metric['item_id_evaluable_slots'], metric['feed_only_unresolved_slots']) == (3, 1, 2)
    assert metric['fill_rate'] == metric['full_triples_rate'] == 1


def test_empty_short_and_unknown_segments():
    m = demo_metrics(['a', 'b'], {'a': {'x'}, 'b': {'x'}}, {'a': {'h'}, 'b': {'h'}},
                     {'h': 'h', 'x': 'x'}, {'a': [], 'b': [{'item_id': None}]})
    assert m['fill_rate'] == .5 and m['full_triples'] == 0
    assert m['precision_at_3_lower_bound'] == 0
    assert m['precision_at_3_upper_bound_loose'] == 1/6
    assert m['item_id_coverage_returned_slots'] == 0
    assert demo_metrics([], {}, {}, {}, {})['precision_at_3_lower_bound'] is None


def test_actual_recommender_output_is_used_without_reranking_or_availability_filter():
    rows = [('1', 'u', date(2026,1,1), 'a', None, 'A', 'g'),
            ('2', 'v', date(2026,1,1), 'a', None, 'A', 'g'),
            ('2', 'v', date(2026,1,1), 'b', None, 'B', 'g'),
            ('3', 'u', date(2026,9,7), 'b', None, 'B', 'g')]
    class Demo:
        pipeline = SimpleNamespace(distinct_clients={'b': 9})

        def recommend_with_catalog_pool(self, user, segment):
            assert (user, segment) == ('u', 'B2B')
            pool = {'feed1': None, 'feed2': None, 'feed3': None, 'target': 'b'}
            return {'candidates': [{'offer_id': k, 'item_id': v} for k, v in pool.items()]}, pool
    r = evaluate(rows, {'u': 'B2B'}, None, Demo())
    d = r['current_feed_retrospective_experiment_NOT_historical_backtest']['A']['all']
    assert d['known_hits'] == 0 and d['feed_only_unresolved_slots'] == 3
    assert r['sales_only_historical']['results']['A']['all']['profile']['hits'] == 1
    paired = r['same_catalog_pool_popularity_control']['A']['all']
    assert paired['popularity']['known_hits'] == 1
    assert paired['popularity']['feed_only_unresolved_slots'] == 2
    assert paired['pool']['known_target_pairs'] == 1
    assert paired['pool']['feed_only_offer_pairs'] == 3
    assert paired['paired_known_hits'] == {'shared': 0, 'catalog_ranker_only': 0, 'popularity_only': 1}


def test_popularity_control_ranks_every_feasible_offer_without_personal_tiebreak():
    pool = {'o2': 'a', 'o1': 'b', 'o3': 'c', 'o-a': None, 'o-z': None}
    counts = {'a': 1, 'b': 1, 'c': 2, 'not-in-pool': 100}
    assert [c['offer_id'] for c in popularity_control(pool, counts)] == ['o3', 'o2', 'o1']
    assert [c['offer_id'] for c in popularity_control({'o-z': None, 'o-a': None, 'o2': 'a'}, counts)] == ['o2', 'o-a', 'o-z']
    assert popularity_control({}, counts) == []
