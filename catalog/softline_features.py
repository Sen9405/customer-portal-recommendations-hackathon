"""Private current-feed feature snapshot; never a historical backtest input.

Run from project root: python3 -m catalog.softline_features data/dataset.xlsx data/softline-features.json
Only aggregate counts go to stdout. Workbook values are never sent to the feed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from catalog.softline_audit import FEED_URL, FeedRedirectHandler, audit_stream, workbook_rows


def feature_snapshot(audit, provenance):
    items = {}
    stats = Counter()
    for item_id, detail in audit['items'].items():
        matches = detail['matches']
        candidates = matches['all_union']
        if len(candidates) != 1:
            continue
        candidate = dict(candidates[0])
        category_id = candidate.get('category_id')
        candidate['category'] = audit['categories'].get(category_id, {'id': category_id, 'parent_id': None, 'name': None}) if category_id else None
        channels = [channel for channel in ('vendorCode', 'url', 'name_exact_N') if matches[channel]]
        items[item_id] = {'match_channels': channels, 'candidate': candidate}
        stats['unique_candidates'] += 1
        stats['with_url'] += bool(candidate.get('url'))
        stats['with_category_id'] += bool(category_id)
        stats['with_category_name'] += bool(candidate['category'] and candidate['category'].get('name'))
        stats['with_vendor'] += bool(candidate.get('vendor'))
        stats['with_params'] += bool(candidate.get('params'))
        stats['available_true'] += candidate.get('available') == 'true'
        stats['available_false'] += candidate.get('available') == 'false'
        stats['available_other_or_missing'] += candidate.get('available') not in ('true', 'false')
    return {'schema_version': 1, 'provenance': provenance, 'feed': audit['feed'],
            'denominator': audit['denominator'], 'matching_summary': audit['summary']['all_union'],
            'statistics': dict(stats),
            'limitations': {'identity': 'Unique matching candidate is not confirmed product identity.',
                            'time': 'Current feed, not a historical point-in-time snapshot; do not use for leakage-free historical evaluation.',
                            'relation': 'Same-category similarity is not confirmed compatibility.'},
            'items': items}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('workbook')
    cli.add_argument('output')
    args = cli.parse_args()
    root = Path('data').resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(root) or output == root:
        cli.error('output must be inside the ignored data directory')
    # Reserve before network access and never overwrite an existing snapshot.
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        started = datetime.now(timezone.utc).isoformat()
        req = Request(FEED_URL, headers={'User-Agent': 'Softline-local-audit/1.0', 'Accept-Encoding': 'identity'})
        with build_opener(FeedRedirectHandler()).open(req, timeout=60) as response:
            final_url = response.geturl()
            target = urlsplit(final_url)
            if target.scheme != 'https' or target.hostname != 'store.softline.ru' or target.port not in (None, 443):
                raise ValueError('Unexpected feed redirect')
            headers = {key: response.headers.get(key) for key in ('Last-Modified', 'ETag', 'Content-Length')}
            result = audit_stream(workbook_rows(args.workbook), response, include_features=True)
        provenance = {'requested_url': FEED_URL, 'response_url': final_url, 'retrieval_started_utc': started,
                      'retrieval_finished_utc': datetime.now(timezone.utc).isoformat(), 'response_headers': headers}
        snapshot = feature_snapshot(result, provenance)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            fd = -1
            json.dump(snapshot, out, ensure_ascii=False, indent=2)
        print(json.dumps({k: snapshot[k] for k in ('provenance', 'feed', 'denominator', 'matching_summary', 'statistics', 'limitations')}, ensure_ascii=False, indent=2))
    except BaseException:
        if fd != -1:
            os.close(fd)
        output.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    main()
