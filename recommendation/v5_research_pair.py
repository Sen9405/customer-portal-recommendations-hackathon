"""Aggregate-only paired evaluation: a historical control and an exploratory injection.

The current researcher has no point-in-time source or identity mapping; it cannot
change official sales-only top-3. The separate experiment runs today's rules,
requires current vendor evidence and a unique train item for each candidate, and
replaces the last slot at most once. Never exposes histories to public tools.
"""
import argparse
from collections import Counter
import json

from recommendation.contest_recommender import ContestRecommender
from recommendation.history_research import from_history, refresh_public_evidence
from recommendation.profile_v4_evaluation import CUTOFF, profile_rank, split_rows
from recommendation.profile_pipeline import _day
from recommendation.public_research import PublicResearch


def inject(base, findings, train_item_by_offer, seen):
    gates = Counter()
    for finding in findings:
        item = train_item_by_offer.get(finding['offer_id'])
        if item is None:
            gates['no_unique_train_item'] += 1
        if finding['source_status'] != 'confirmed' or not finding['url']:
            gates['no_confirmed_public_evidence'] += 1
        if item is None or finding['source_status'] != 'confirmed' or not finding['url'] or finding['status'] == 'rejected':
            continue
        if item in seen or item in base:
            gates['owned_or_already_ranked'] += 1
            continue
        return [*base[:2], item], gates
    return base, gates


def compare(rows, groups, rec, evidence):
    train, hist, future = split_rows(rows)
    names = {}
    for row in rows:
        if _day(row[2]) <= CUTOFF and row[5]:
            names.setdefault(row[3], str(row[5]))
    train_item_by_offer = {offer['offer_id']: item for item in {r[3] for r in train}
                           if (offer := rec._unique(item))}
    result = {cohort: Counter() for cohort in ('A', 'B')}
    for user, seen in hist.items():
        if not (target := future.get(user, set()) - seen):
            continue
        base = profile_rank(rec.pipeline, user, 'full')
        hypotheses = from_history(rec, user, names, groups.get(user), evidence)
        experimental, gates = inject(base, hypotheses, train_item_by_offer, seen)
        before, after = set(base) & target, set(experimental) & target
        for cohort in ('A', 'B') if len(seen) == 1 else ('A',):
            result[cohort].update(clients=1, sales_only_profile_hits=len(before),
                                  exploratory_hits=len(after), clients_with_hypothesis=bool(hypotheses), hypotheses=len(hypotheses),
                                  exploratory_lists_changed=base != experimental, added_hits=len(after-before), lost_hits=len(before-after))
            result[cohort].update(gates)
    return {name: {**counts, 'precision_denominator': 3 * counts['clients']} for name, counts in result.items()}


def main():
    import openpyxl
    p = argparse.ArgumentParser(description=__doc__)
    for field in ('xlsx','mapping','index'):
        p.add_argument(field)
    p.add_argument('--fetch-public', action='store_true', help='fixed vendor pages only, no client input')
    a = p.parse_args()
    evidence = refresh_public_evidence(PublicResearch()) if a.fetch_public else {}
    w = openpyxl.load_workbook(a.xlsx, read_only=True, data_only=True)
    try:
        rows = list(w['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in w['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        w.close()
    rec = ContestRecommender(split_rows(rows)[0], CUTOFF, a.mapping, a.index, workbook_path=a.xlsx)
    print(json.dumps({'cohorts':compare(rows, groups, rec, evidence),
                      'public_loads': sum(t['outcome'] == 'fetched' for r in evidence.values() for t in r['trace']),
                      'historical_research_replacements':0, 'llm_calls':0,
                      'limitations':'Known v5 test window. Historical control cannot use current vendor pages, catalog or mapping. Exploratory injection uses all three and is not a historical estimate. Zero replacements means impact is unmeasured, not that research has zero value.'}, indent=2))


if __name__ == '__main__':
    main()
