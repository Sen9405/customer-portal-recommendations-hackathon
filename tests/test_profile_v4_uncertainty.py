from datetime import date

import pytest

from recommendation.profile_v4_evaluation import run as old_run
from recommendation.profile_v4_uncertainty import percentile, run, summarize


def test_paired_identical_and_fixed_denominator():
    result = summarize([(1, 1, 0), (0, 0, 0)], 1000, 11)
    assert result == summarize([(1, 1, 0), (0, 0, 0)], 1000, 11)
    assert result['denominator'] == 6
    assert result['methods']['profile']['hits'] == 1
    assert result['methods']['profile']['precision_at_3'] == 1 / 6
    assert result['differences']['popularity']['bootstrap_95_percentile'] == [0, 0]
    assert result['differences']['copurchase']['profile_minus_baseline'] == 1 / 6


def test_empty_small_and_invalid():
    assert summarize([], 2)['methods'] is None
    assert summarize([(2, 1, 0)], 20)['differences']['popularity']['bootstrap_95_percentile'] == [1/3, 1/3]
    assert percentile([0, 10], .25) == 2.5
    with pytest.raises(ValueError):
        summarize([(4, 0, 0)])
    with pytest.raises(ValueError):
        summarize([], 0)


def test_matches_existing_evaluation_and_segments():
    rows = [('o1', 'u1', date(2026, 8, 1), 'a'),
            ('o1', 'u2', date(2026, 8, 1), 'b'),
            ('o2', 'u3', date(2026, 8, 1), 'c'),
            ('o3', 'u1', date(2026, 9, 7), 'b'),
            ('o4', 'u2', date(2026, 9, 7), 'c'),
            ('o5', 'u3', date(2026, 9, 7), 'a')]
    groups = {'u1': 'B2B', 'u2': 'B2C'}
    got, reference = run(rows, groups, 100, 4), old_run(rows, groups)
    for cohort in ('A', 'B'):
        assert got[cohort]['other_or_unknown']['clients'] == 1
        for segment in ('all', 'B2B', 'B2C'):
            for method, old in [('profile', 'profile_full'), ('popularity', 'popularity'), ('copurchase', 'copurchase')]:
                assert got[cohort][segment]['methods'][method]['hits'] == reference[cohort][segment][old]['matches']
                assert got[cohort][segment]['clients'] == reference[cohort][segment][old]['clients']
