import json
import sqlite3
from datetime import date

import pytest

from recommendation.family_evidence_experiment import (
    REGISTRY, aggregate, family_mentioned, full_catalog_aggregate, historical_evaluation, make_candidates, mapped_names,
)


def registry():
    with REGISTRY.open(encoding='utf8') as f:
        return json.load(f)


def test_literal_family_mentions_and_boundaries():
    assert family_mentioned('License Veeam ONE 13 Enterprise', 'Veeam ONE 13')
    assert not family_mentioned('License Veeam ONE 130 Enterprise', 'Veeam ONE 13')
    assert not family_mentioned('Veeam ONE', 'Veeam ONE 13')
    assert not family_mentioned(None, 'Veeam ONE 13')


def test_conditional_direction_and_evidence_not_identity():
    items = {'source': 'Veeam ONE 13', 'target': 'Veeam Backup & Replication'}
    edges = make_candidates(items, registry())
    evidence = edges['source']['target'][0]
    assert evidence['relation_id'] == 'veeam-one13-backup-monitoring'
    assert evidence['url'].startswith('https://helpcenter.veeam.com/')
    assert evidence['backtest_eligible'] is False
    assert evidence['status'] == 'conditional_family_hypothesis_not_identity'
    assert not edges.get('target')
    assert not make_candidates({'same': 'Veeam ONE 13 Veeam Backup & Replication'}, registry())


def test_historical_never_accepts_claimed_old_dates():
    with pytest.raises(ValueError, match='historical evaluation unavailable'):
        historical_evaluation({'retrieved_at': '2020-01-01'}, cutoff='2026-08-31')


def test_candidate_filter_ambiguous_shared_and_mismatched():
    conn = sqlite3.connect(':memory:')
    conn.execute('create table offers (row_id integer, offer_id text, name text)')
    conn.executemany('insert into offers values (?,?,?)', [(1,'a','A'),(2,'b','B'),(3,'c','C')])
    def c(row, offer):
        return {'row_id': row, 'offer_id': offer}
    mapping = {'items': {'good': {'candidates':[c(1,'a')]},
                         'shared1': {'candidates':[c(2,'b')]},
                         'shared2': {'candidates':[c(2,'b')]},
                         'ambiguous': {'candidates':[c(2,'b'), c(3,'c')]},
                         'missing': {'candidates':[]}}}
    assert mapped_names(mapping, conn) == {'good': 'A'}
    mapping['items']['good']['candidates'][0]['offer_id'] = 'not-a'
    with pytest.raises(ValueError, match='mismatch'):
        mapped_names(mapping, conn)


def test_aggregate_cold_pair_and_no_future_sales_in_history():
    rows = [('o1','u',date(2026,8,31),'a'), ('o2','u',date(2026,9,7),'b')]
    result = aggregate(rows, {'a':'Veeam ONE 13', 'b':'Veeam Backup & Replication'}, registry())
    assert result['counts']['cold_target_pairs'] == 1
    assert result['counts']['cold_reachable'] == 1
    assert result['counts']['cold_top3'] == 1
    assert result['historical_evaluation'] == 'blocked'
    assert result['backtest_eligible'] is False


def test_full_feed_counts_hypotheses_without_backtest():
    conn = sqlite3.connect(':memory:')
    conn.execute('create table offers (row_id integer, name text)')
    conn.executemany('insert into offers values (?,?)', [(1, 'Veeam Backup & Replication'), (2, 'Other')])
    rows = [('o1', 'u', date(2026, 8, 31), 'a'), ('o2', 'u', date(2026, 9, 7), 'b')]
    result = full_catalog_aggregate(rows, {'a': 'Veeam ONE 13'}, registry(), conn)
    assert result['counts']['clients_with_catalog_hypotheses'] == 1
    assert result['counts']['summed_distinct_offer_occurrences_per_client'] == 1
    assert not result['backtest_eligible']
