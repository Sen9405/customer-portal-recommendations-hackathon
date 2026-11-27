"""Offline candidate mapping against a saved catalog index; never identity approval."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from catalog.softline_audit import normalize_sku, url_sku, workbook_rows


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build(rows, conn):
    items, sku, names = {}, defaultdict(set), defaultdict(set)
    for item_id, code, name in rows:
        if item_id is None or not str(item_id).strip():
            continue
        item_id = str(item_id)
        items.setdefault(item_id, {'rows': 0, 'candidates': []})['rows'] += 1
        if item_id.startswith('N-'):
            if name and str(name).strip():
                names[str(name).strip()].add(item_id)
        elif normalize_sku(code):
            sku[normalize_sku(code)].add(item_id)
    id_counts = dict(conn.execute('SELECT offer_id, count(*) FROM offers WHERE offer_id IS NOT NULL AND offer_id != "" GROUP BY offer_id HAVING count(*) > 1'))
    availability = Counter()
    for row_id, offer_id, code, url, name, available in conn.execute('SELECT row_id,offer_id,vendor_code,url,name,available FROM offers ORDER BY row_id'):
        availability[available if available in ('true', 'false') else 'other_or_missing'] += 1
        channels = defaultdict(list)
        for channel, ids in [('vendorCode', sku.get(normalize_sku(code), ())), ('url', sku.get(url_sku(url), ())), ('name_exact_N', names.get(name, ()) )]:
            for item in ids:
                channels[item].append(channel)
        for item, evidence in channels.items():
            items[item]['candidates'].append({'row_id': row_id, 'offer_id': offer_id, 'match_channels': evidence, 'available': available, 'duplicate_feed_offer_id': bool(offer_id and offer_id in id_counts)})
    counts = Counter()
    unique_offers = Counter()
    for detail in items.values():
        candidates = detail['candidates']
        state = 'no_candidate' if not candidates else 'unique' if len(candidates) == 1 else 'ambiguous'
        counts[state] += 1
        if state == 'unique':
            c = candidates[0]
            counts['unique_available_' + (c['available'] if c['available'] in ('true', 'false') else 'other_or_missing')] += 1
            counts['unique_duplicate_feed_offer_id'] += c['duplicate_feed_offer_id']
            if c['offer_id']:
                unique_offers[c['offer_id']] += 1
            else:
                counts['unique_missing_offer_id'] += 1
    counts['unique_items_shared_offer_id'] = sum(n for n in unique_offers.values() if n > 1)
    counts['unique_shared_offer_id_values'] = sum(n > 1 for n in unique_offers.values())
    return {'denominator': {'items': len(items), 'rows': sum(x['rows'] for x in items.values())},
            'summary': dict(counts), 'feed': {'offers': sum(availability.values()), 'availability': dict(availability), 'duplicate_offer_id_values': len(id_counts), 'rows_with_duplicate_offer_id': sum(id_counts.values())}, 'items': items}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('workbook'); p.add_argument('index'); p.add_argument('output')
    args = p.parse_args()
    root = Path('data').resolve()
    paths = [Path(x).resolve(strict=True) for x in (args.workbook, args.index)]
    output = Path(args.output).resolve()
    if not all(x.is_relative_to(root) for x in paths) or not output.is_relative_to(root) or output == root:
        p.error('all paths must be inside data/')
    if not output.parent.is_dir():
        p.error('output parent must exist')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with sqlite3.connect(paths[1].as_uri() + '?mode=ro&immutable=1', uri=True) as conn:
            metadata = dict(conn.execute('SELECT key,value FROM metadata'))
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('index integrity check failed')
            result = build(workbook_rows(paths[0]), conn)
        result['provenance'] = {'workbook_sha256': digest(paths[0]), 'index_sha256': digest(paths[1]), 'index_metadata': metadata, 'generated_utc': datetime.now(timezone.utc).isoformat(), 'method': 'all distinct feed occurrences matched by normalized vendorCode or URL SKU; N- items by exact trimmed name; union of channels; distinct rows never merged'}
        result['limitations'] = ['Candidates are not approved identities or evidence of compatibility.', 'Availability is from the indexed feed snapshot, not live or historical.', 'Current snapshot may introduce future information in historical evaluation.']
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            fd = -1
            json.dump(result, out, ensure_ascii=False, indent=2)
        print(json.dumps({k: result[k] for k in ('denominator', 'summary', 'feed')}, ensure_ascii=False))
    except BaseException:
        if fd != -1:
            os.close(fd)
        output.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    main()
