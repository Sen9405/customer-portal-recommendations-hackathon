"""Prioritize pending identity reviews using training history only, offline.

Run: python -m catalog.review_priority data/dataset.xlsx data/identity-review.sqlite
Optional --private-output data/new-file.json writes a detailed queue, owner-only,
without overwriting. Stdout contains aggregates only. The ranking is triage, not
identity proof or automatic approval. Candidate counts come from a current audit,
not an as-of-cutoff snapshot; customer group metadata is also undated. Neither
should be treated as point-in-time features or as evidence of future impact.
Cold means exactly one distinct training item, not the test-defined cohort B.
Historical coverage favors popular/established products and excludes true new
customers/items; unique-candidate-first further deprioritizes hard cases.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime
import json
import os
from pathlib import Path
import sqlite3

from catalog.identity_review import _secure

CUTOFF = date(2026, 8, 31)


def prioritize(sales, groups, cases):
    """Return private ordered records and aggregate coverage; never use test rows."""
    history = defaultdict(set)
    for user, week, item in sales:
        if isinstance(week, datetime):
            week = week.date()
        elif isinstance(week, str):
            week = date.fromisoformat(week)
        if not isinstance(week, date):
            raise ValueError('Invalid sale date')
        if week > CUTOFF:
            continue
        if not user or not item:
            raise ValueError('Invalid training sale')
        history[user].add(item)
    users_by_item = defaultdict(set)
    cold_by_item = defaultdict(set)
    for user, items in history.items():
        for item in items:
            users_by_item[item].add(user)
            if len(items) == 1 and groups.get(user) == 'B2B':
                cold_by_item[item].add(user)
    records = []
    for item, candidates, status in cases:
        if status is not None:
            continue
        if candidates not in (0, 1, 2):
            raise ValueError('Invalid candidate count')
        records.append({'item_id': item, 'candidate_count': candidates,
                        'train_users': len(users_by_item[item]),
                        'b2b_cold_train_users': len(cold_by_item[item])})
    # Unique candidates are the only cases currently exportable after review;
    # ties are deterministic, not evidence of quality or identity.
    records.sort(key=lambda r: (r['candidate_count'] != 1,
                                -r['b2b_cold_train_users'], -r['train_users'], str(r['item_id'])))
    def coverage(rows):
        ids = [r['item_id'] for r in rows]
        return {'cases': len(ids), 'unique_candidate_cases': sum(r['candidate_count'] == 1 for r in rows),
                'cases_with_train_history': sum(bool(users_by_item[i]) for i in ids),
                'distinct_train_users': len(set().union(*(users_by_item[i] for i in ids))),
                'distinct_b2b_cold_train_users': len(set().union(*(cold_by_item[i] for i in ids)))}
    aggregate = {'cutoff_inclusive': CUTOFF.isoformat(), 'pending': coverage(records),
                 'top_25': coverage(records[:25]), 'top_100': coverage(records[:100]),
                 'note': 'Triage only; current candidate audit and undated B2B metadata may leak dates; coverage is not measured benefit.'}
    return records, aggregate


def read_cases(path):
    _secure(path)
    uri = Path(path).resolve(strict=True).as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        return list(conn.execute('''SELECT c.item_id,c.candidate_count,e.status
          FROM cases c LEFT JOIN events e ON e.seq=(SELECT MAX(x.seq) FROM events x WHERE x.item_id=c.item_id)'''))
    finally:
        conn.close()


def private_output(path, records, root):
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    if not parent.is_relative_to(root) or path.name in ('', '.', '..'):
        raise ValueError('Output must be inside data/')
    target = parent / path.name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(records, stream, ensure_ascii=False, indent=2)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook')
    parser.add_argument('database')
    parser.add_argument('--private-output')
    args = parser.parse_args()
    root = Path('data').resolve(strict=True)
    for value in (args.workbook, args.database):
        if not Path(value).resolve(strict=True).is_relative_to(root):
            parser.error('Inputs must be inside data/')
    import openpyxl
    wb = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    try:
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
        sales = ((r[1], r[2], r[3]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True))
        records, aggregate = prioritize(sales, groups, read_cases(args.database))
    finally:
        wb.close()
    if args.private_output:
        private_output(args.private_output, records, root)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
