import pytest

from recommendation.hybrid_evaluation import evaluate_window


def test_strict_run_rejects_content():
    with pytest.raises(ValueError):
        evaluate_window([], [], '2026-01-01', '2026-02-01', None,
                        {'a': {'b'}}, content_label='none')


def test_cold_evidence_and_lexical_ablation():
    rows = [('1','u','2026-01-01','a'), ('2','v','2026-01-01','a'),
            ('3','v','2026-01-08','b'), ('4','u','2026-02-01','cold')]
    names = ['Product version 1', 'Product version 1', 'Product version 2', 'future name']
    result = evaluate_window(rows, names, '2026-01-08', '2026-02-01', None,
                             {'a': {'cold'}}, content_label='synthetic')
    a = result['A']
    assert a['history']['cold_hits'] == 0
    assert a['hybrid']['cold_hits'] == 1
    assert a['content_only']['users_lt3'] == 1
    assert a['hybrid']['hits'] == 1
    assert a['history_lexical']['slots_from_history'] == 0
    assert a['hybrid']['cold_targets'] == 1


def test_empty_content_cannot_invent_cold_evidence():
    rows = [('1','u','2026-01-01','a'), ('2','u','2026-02-01','b')]
    r = evaluate_window(rows, ['a','b'], '2026-01-01', '2026-02-01', None, {}, content_label='none')
    assert r['A']['hybrid']['cold_hits'] == 0
    assert r['A']['hybrid']['users_lt3'] == 1
    assert r['A']['hybrid']['slots'] == 0
