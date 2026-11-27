"""Aggregate-only v5 comparison. Run with workbook and original baseline paths.

The original baseline is executable Python: supply only a trusted local file. Its
stdout is captured; per-client lists never leave this process. Product groups
follow the original's last-row-wins rule and are used ONLY for hit reporting,
not for training or recommendation. They are undated and may contain future data.
"""
import argparse
import contextlib
import io
import json
import runpy
import sys

from recommendation.profile_v4_evaluation import (
    CUTOFF, split_rows, official_models, official_rank, profile_rank,
)
from recommendation.profile_pipeline import ProfilePipeline


def compare(rows, groups, original=None):
    train, hist, future = split_rows(rows)
    # Reproduce the organizer's whole-workbook, last-row-wins group semantics.
    pgroup = {r[3]: (r[6] if len(r) > 6 and r[6] else r[3]) for r in rows}
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    cohorts = {'A': list(targets), 'B': [u for u in targets if len(hist[u]) == 1]}
    pop, co = official_models(train, hist)
    pipeline = ProfilePipeline(train, CUTOFF)
    methods = {'popularity': lambda u: official_rank(u, hist, pop),
               'copurchase': lambda u: official_rank(u, hist, pop, co),
               'profile': lambda u: profile_rank(pipeline, u, 'full')}
    results = {}
    mismatches = {'popularity': 0, 'copurchase': 0}
    metric_mismatches = {'popularity': 0, 'copurchase': 0}
    for cohort, users in cohorts.items():
        results[cohort] = {}
        for segment in ('all', 'B2B', 'B2C'):
            selected = [u for u in users if segment == 'all' or groups.get(u) == segment]
            metrics = {}
            for method, rank in methods.items():
                hits = variant = nonempty = full = 0
                for u in selected:
                    rec = rank(u)
                    hit = set(rec) & targets[u]
                    own = {pgroup[i] for i in hist[u]}
                    hits += len(hit)
                    variant += sum(pgroup[i] in own for i in hit)
                    nonempty += bool(rec)
                    full += len(rec) == 3
                    if original is not None and segment == 'all' and method != 'profile':
                        reference = original['rec_' + method](u)
                        mismatches[method] += rec != reference
                n = len(selected)
                metrics[method] = {'clients': n, 'hits': hits, 'denominator': 3*n,
                    'precision_at_3': hits/(3*n) if n else None,
                    'variants': variant, 'new': hits-variant,
                    'variant_share': variant/hits if hits else None,
                    'nonempty': nonempty, 'fill_rate': nonempty/n if n else None,
                    'full_triples': full, 'full_triples_rate': full/n if n else None}
            if original is not None and selected:
                for method in ('popularity', 'copurchase'):
                    ref_method = original['rec_' + method]
                    ref_p, ref_fill = original['evaluate'](ref_method, selected)
                    ref_split = original['hit_split'](ref_method, selected)
                    actual = metrics[method]
                    expected_share = round(100 * actual['variant_share'], 1) if actual['variant_share'] is not None else None
                    if (round(100 * actual['precision_at_3'], 2), round(100 * actual['fill_rate'], 1),
                        actual['hits'], expected_share) != (ref_p, ref_fill, ref_split['hits'], ref_split['variant_share_%']):
                        metric_mismatches[method] += 1
            results[cohort][segment] = metrics
    return {'results': results, 'original_list_mismatches_A_plus_B': mismatches if original is not None else None,
            'original_metric_mismatches_segments': metric_mismatches if original is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx')
    parser.add_argument('baseline', help='trusted original organizer baseline Python file')
    args = parser.parse_args()
    import openpyxl
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    old_argv = sys.argv
    try:
        sys.argv = [args.baseline, args.xlsx]
        with contextlib.redirect_stdout(io.StringIO()):
            original = runpy.run_path(args.baseline)
    finally:
        sys.argv = old_argv
    result = compare(rows, groups, original)
    result['limitations'] = ('Observed test, not independent; profile tuned on v3. '
        'product_group follows original full workbook and is undated: reporting only, '
        'potential future information. Customer groups undated; availability unknown. '
        'Profile baskets differ from official. No current catalog or model calls.')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(result['original_list_mismatches_A_plus_B'].values()) or any(result['original_metric_mismatches_segments'].values()):
        raise SystemExit('Original baseline lists do not match')


if __name__ == '__main__':
    main()
