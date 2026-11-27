from collections import Counter
from types import SimpleNamespace

from recommendation.v5_offline_experiments import diversify, sequential_rank, evaluate_window
from recommendation.v5_mapping_loss import diagnose, exact_probes, loss_reason


def test_source_normalization_and_loo_denominator():
    graph = {'x': Counter(a=10, z=100), 'y': Counter(b=5)}
    args = (graph, {'y', 'x'}, {'x', 'y', 'z'}, ['fallback'])
    assert sequential_rank(*args) == ['a', 'b', 'fallback']
    assert sequential_rank(*args, normalized=True) == ['b', 'a', 'fallback']
    assert sequential_rank(*args, normalized=True, own={'x': {'z': 100}}) == ['a', 'b', 'fallback']
    assert sequential_rank(*args, normalized=True, edge_ok=lambda s, t: t != 'z') == ['a', 'b', 'fallback']


def test_diversify_defers_near_duplicates_without_losing_fill():
    similar = lambda a, b: {a, b} == {'a', 'b'}
    assert diversify(['a', 'b', 'c', 'd'], similar) == ['a', 'c', 'd']
    assert diversify(['a', 'b', 'c'], similar) == ['a', 'c', 'b']
    assert diversify(['a', 'b'], similar) == ['a', 'b']
    assert diversify(['a', 'b', 'c', 'd'], similar, head=3) == ['a', 'c', 'b']


def test_window_changes_post_label_do_not_change_output_and_cold_target_stays_in_denominator():
    train = [('1', 'test', '2026-05-25', 'a', 'Existing product'),
             ('2', 'other', '2026-05-25', 'b', 'Unrelated name')]
    labels = [('3', 'test', '2026-06-01', 'cold', 'Future-only name')]
    window = ('2026-05-25', '2026-06-01', '2026-06-01')
    first = evaluate_window(train + labels, window)
    later = [('4', 'test', '2026-06-08', 'b', 'Existing product')]
    assert first == evaluate_window(train + labels + later, window)
    assert first['cohorts']['A']['denominator'] == 3
    assert first['cohorts']['A']['cold_targets'] == 1
    assert first['cohorts']['A']['methods']['sequential_raw']['hits'] == 0
    assert first['cohorts']['B']['clients'] == 1


def test_exact_probes_count_union_occurrences_not_channels():
    sales = {'a': {'skus': {'X17'}, 'names': {'Example name'}},
             'b': {'skus': set(), 'names': {'elsewhere'}}}
    offers = [('X17', '', ' Example name '), ('', '', 'Example name'), ('', '', 'unrelated')]
    a, b = (exact_probes(sales, offers)[key] for key in ('a', 'b'))
    assert a == Counter(vendor_code=1, exact_trimmed_name=2, union_occurrences=2)
    assert not b


def test_loss_reason_disjoint_precedence():
    unique = lambda item: {'offer_id': 'x', 'availability': 'true'} if item == 'a' else None
    entries = {'a': {'candidates': [{'offer_id': 'x'}]}, 'bad': {'candidates': [{}]}}
    assert loss_reason('missing', entries, unique, {'x'}) == 'no_mapping_candidate'
    assert loss_reason('bad', entries, unique, set()) == 'ambiguous_or_unusable'
    assert loss_reason('a', entries, unique, {'x'}) == 'history_offer_exclusion'
    assert loss_reason('a', entries, unique, set(), lambda _: None) == 'snapshot_policy_exclusion'
    assert loss_reason('a', entries, unique, set()) == 'eligible_not_top3'


def test_mapping_reconciliation_separates_exact_probe_from_hits():
    rows = [('1', 'test', '2026-08-31', 'owned', None, None),
            ('2', 'other', '2026-08-31', 'target', None, 'Example product'),
            ('3', 'test', '2026-09-07', 'target', 'future sku', 'future name')]
    rec = SimpleNamespace(items={}, _unique=lambda _: None,
                          recommend=lambda user, seg: {'candidates': [{'item_id': None}]},
                          pipeline=SimpleNamespace(blended_candidates=lambda u: [{'item_id': 'target'}]))
    offers = [('', '', 'Example product'), ('future sku', '', 'future name')]
    result = diagnose(rows, {}, rec, offers)
    assert result['A'] == result['B']
    assert result['A']['lost_first_blocker']['no_mapping_candidate'] == 1
    assert result['A']['carousel_known_hits'] == 0
    assert result['A']['unknown_item_id_slots'] == 1
    assert result['A']['exact_probe']['one_occurrence_hits'] == 1
    assert result['A']['exact_probe']['sku_present_hits'] == 0
