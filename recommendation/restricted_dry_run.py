"""Private offline audit: python -B -m recommendation.restricted_dry_run.

Aggregate-only stdout, no output files or network. Preview is a separate,
non-serving diagnostic, NOT Hybrid output or checked availability. Identity
reviews do not attest history completeness or aliases. Current snapshot is not
historical evidence. The actual Hybrid is called with no checker and without
asserting history completeness; it must refuse. No individual IDs are printed.
"""
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3

from catalog.identity_review import attestations
from recommendation.experimental_graph import fit
from recommendation.hybrid import Hybrid, MAX_HISTORY, possible_from_audit


def audit(model, conn, reviewed, possible):
    registry = json.loads((Path(__file__).resolve().parent.parent / 'sources/manufacturer_relations.json').read_text(encoding='utf-8'))
    engine = Hybrid(model, conn, {'schema_version': 1, 'items': {}}, registry, [],
                    possible_matches=possible, verified_identities=reviewed,
                    availability_check=None)
    reasons, preview_reasons, explanations = Counter(), Counter(), Counter()
    preview_count = reviewed_histories = production_count = 0

    def unique_row(offer):
        rows = conn.execute('SELECT available FROM offers WHERE offer_id=? LIMIT 2', (offer,)).fetchall()
        return rows[0] if len(rows) == 1 else None

    def resolved(item):
        offer = reviewed.get(item)
        return bool(offer and not (set(possible.get(item, ())) - {offer}) and unique_row(offer) is not None)

    for history in model['history'].values():
        if len(history) > MAX_HISTORY:
            reasons['history_over_limit'] += 1
            preview_reasons['history_over_limit'] += 1
            continue
        outcome = engine.recommend(history, history_complete=False)
        reasons[outcome['blocked_reason'] or 'not_blocked'] += 1
        production_count += len(outcome['recommendations'])
        if not all(resolved(item) for item in history):
            preview_reasons['history_identity_unresolved_or_offer_invalid'] += 1
            continue
        reviewed_histories += 1
        # This preview models neither real-time availability nor full Hybrid
        # ranking. Only explicitly reviewed sales identities may be candidates.
        candidates = Counter()
        for item in sorted(history):
            for target, count in model['pairs'].get(item, {}).items():
                if target not in history:
                    candidates[target] += count
        ordered = [(x, 'co_order_association') for x in sorted(candidates, key=lambda x: (-candidates[x], str(x)))]
        ordered += [(x, 'training_popularity') for x in model['ranking'] if x not in candidates and x not in history]
        selected = {reviewed[x] for x in history}
        n = 0
        for target, explanation in ordered:
            if not resolved(target):
                continue
            offer = reviewed[target]
            row = unique_row(offer)
            if offer in selected or row is None or row[0] != 'true':
                continue
            selected.add(offer)
            n += 1
            explanations[explanation] += 1
            if n == 3:
                break
        preview_count += n
        if not n:
            preview_reasons['no_reviewed_snapshot_eligible_candidates'] += 1
    return {'mode': 'OFFLINE_DIAGNOSTIC_NOT_CUSTOMER_FACING',
            'availability': 'NOT_CHECKED_NO_PROVIDER', 'reviewed_identities': len(reviewed),
            'histories': len(model['history']), 'reviewed_histories': reviewed_histories,
            'production_blocked': dict(reasons), 'production_recommendations': production_count,
            'preview_candidates': preview_count, 'preview_explanations': dict(explanations),
            'preview_blocked': dict(preview_reasons),
            'limitations': ['Preview candidates are hypothetical, not Hybrid recommendations; snapshot true is not live availability.',
                            'History completeness and product aliases are not attested; never serve these candidates.',
                            'Co-order means association, not compatibility; order IDs can recur across weeks.',
                            'Current catalog and reviews are not valid for historical backtesting.']}


def main():
    root = Path('data').resolve(strict=True)
    paths = [root / x for x in ('dataset.xlsx', 'identity-review.sqlite', 'softline-audit.json', 'catalog-20261126.sqlite')]
    for path in paths:
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('Inputs must stay inside data/')
    import openpyxl
    workbook = openpyxl.load_workbook(paths[0], read_only=True, data_only=True)
    try:
        train = []
        for r in workbook['Продажи'].iter_rows(min_row=2, values_only=True):
            week = r[2].date() if isinstance(r[2], datetime) else r[2]
            if isinstance(week, str):
                week = date.fromisoformat(week)
            if not isinstance(week, date):
                raise ValueError('Invalid date')
            if week <= date(2026, 8, 31):
                train.append((r[0], r[1], week, r[3]))
        model = fit(train)
    finally:
        workbook.close()
    reviewed = attestations(paths[1])
    with paths[2].open(encoding='utf-8') as f:
        possible = possible_from_audit(json.load(f))
    conn = sqlite3.connect(paths[3].resolve().as_uri() + '?mode=ro', uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        print(json.dumps(audit(model, conn, reviewed, possible), ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Offline audit failed; check private inputs locally. No details emitted.') from None
