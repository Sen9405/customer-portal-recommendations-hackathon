"""Local-only audit of the current Softline feed against workbook sales.

Run: python3 -m catalog.softline_audit data/dataset.xlsx data/softline-audit.json
The result contains private item IDs and must remain in the ignored data directory.
SKU equality is a candidate match, not proof of product identity.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.parsers import expat

FEED_URL = 'https://store.softline.ru/feeds/Mindbox.yml.gz'
CHANNELS = ('vendorCode', 'url', 'name_exact_N', 'sku_union', 'all_union')


class FeedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.hostname != 'store.softline.ru' or target.port not in (None, 443):
            raise ValueError('Unexpected feed redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalize_sku(value):
    return ''.join(c for c in str(value or '').upper() if c.isalnum())


def url_sku(url):
    path = unquote(urlsplit(url or '').path).rstrip('/')
    match = re.fullmatch(r'(.+)-[0-9]+', path.rsplit('/', 1)[-1])
    return normalize_sku(match.group(1)) if match else ''


def audit_stream(rows, compressed, max_xml_bytes=1024 * 1024 * 1024, *, include_features=False, max_params=512):
    """Stream gzip and XML; memory bounded by local item keys + two matches/item.

    A DOCTYPE declaration is allowed because the provider emits one; entities
    (including internal definitions) are forbidden and external DTD loading is off.
    """
    items, sku_index, name_index = {}, defaultdict(set), defaultdict(set)
    for item_id, sku, name in rows:
        if item_id is None or not str(item_id).strip():
            continue
        item_id = str(item_id)
        item = items.setdefault(item_id, {'rows': 0, 'matches': {k: {} for k in CHANNELS}})
        item['rows'] += 1
        if item_id.startswith('N-'):
            if name and str(name).strip():
                name_index[str(name).strip()].add(item_id)
        elif normalize_sku(sku):
            sku_index[normalize_sku(sku)].add(item_id)

    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    def reject(*args):
        raise ValueError('Entity declarations and external entities are forbidden')
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    depth = 0
    capture = None
    chunks = []
    length = 0
    offer = None
    offers = 0
    available = 0
    feed_date = None
    categories = {}
    capture_attrs = None

    def start(tag, attrs):
        nonlocal depth, capture, chunks, length, offer, feed_date, capture_attrs
        depth += 1
        if depth > 64:
            raise ValueError('XML nesting limit exceeded')
        if tag == 'yml_catalog':
            feed_date = attrs.get('date')
        if tag == 'offer':
            if offer is not None:
                raise ValueError('Nested offers are invalid')
            offer = {'id': attrs.get('id'), 'available': attrs.get('available'), 'params': []}
        elif include_features and tag == 'category' and offer is None and capture is None:
            if len(categories) >= 100000:
                raise ValueError('Category limit exceeded')
            capture, chunks, length, capture_attrs = tag, [], 0, dict(attrs)
        elif offer is not None and tag in (('vendorCode', 'url', 'name', 'categoryId', 'vendor', 'param') if include_features else ('vendorCode', 'url', 'name')) and capture is None:
            capture, chunks, length = tag, [], 0
            capture_attrs = dict(attrs)
            if sum(len(k) + len(v) for k, v in capture_attrs.items()) > 65536:
                raise ValueError('Offer attributes limit exceeded')

    def text(data):
        nonlocal length
        if capture is not None:
            length += len(data)
            if length > 65536:
                raise ValueError('Offer field size limit exceeded')
            chunks.append(data)

    def record(ids, channel, key, candidate):
        for item_id in ids:
            found = items[item_id]['matches'][channel]
            # Only retain two distinct candidates: enough to classify ambiguous.
            if key in found or len(found) < 2:
                found[key] = candidate

    def end(tag):
        nonlocal depth, capture, chunks, offer, offers, available, capture_attrs
        if capture == tag:
            value = ''.join(chunks).strip()
            if tag == 'category':
                if capture_attrs.get('id'):
                    categories[capture_attrs['id']] = {'id': capture_attrs['id'], 'parent_id': capture_attrs.get('parentId'), 'name': value}
            elif tag == 'param':
                if len(offer['params']) >= max_params:
                    raise ValueError('Offer parameter limit exceeded')
                offer['params'].append({'name': capture_attrs.get('name'), 'unit': capture_attrs.get('unit'), 'value': value})
            else:
                offer[tag] = value
            capture, chunks = None, []
        if tag == 'offer' and offer is not None:
            offers += 1
            available += offer['available'] == 'true'
            # Distinct XML offers remain distinct even when feed IDs collide.
            key = offers
            candidate = {'offer_id': offer.get('id'), 'url': offer.get('url'), 'available': offer.get('available')}
            if include_features:
                candidate.update({'category_id': offer.get('categoryId'), 'vendor': offer.get('vendor'), 'params': offer['params']})
            v = sku_index.get(normalize_sku(offer.get('vendorCode')), set())
            u = sku_index.get(url_sku(offer.get('url')), set())
            n = name_index.get(offer.get('name', ''), set())
            for channel, ids in (('vendorCode', v), ('url', u), ('name_exact_N', n), ('sku_union', v | u), ('all_union', v | u | n)):
                record(ids, channel, key, candidate)
            offer = None
        depth -= 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    total = 0
    with gzip.GzipFile(fileobj=compressed) as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_xml_bytes:
                raise ValueError('Decompressed XML size limit exceeded')
            parser.Parse(chunk, False)
        parser.Parse(b'', True)
    summary = {}
    for channel in CHANNELS:
        counts = {state: {'items': 0, 'rows': 0} for state in ('unmatched', 'unique', 'ambiguous')}
        for item in items.values():
            n = len(item['matches'][channel])
            state = 'unmatched' if n == 0 else 'unique' if n == 1 else 'ambiguous'
            counts[state]['items'] += 1
            counts[state]['rows'] += item['rows']
        summary[channel] = counts
    details = {item_id: {'rows': item['rows'], 'matches': {channel: list(matches.values()) for channel, matches in item['matches'].items()}} for item_id, item in items.items()}
    result = {'feed': {'date_as_supplied': feed_date, 'offers': offers, 'available_true': available, 'xml_bytes': total},
            'denominator': {'items': len(items), 'rows': sum(x['rows'] for x in items.values()), 'N_items': sum(k.startswith('N-') for k in items)},
            'summary': summary, 'items': details}
    if include_features:
        result['categories'] = categories
    return result


def workbook_rows(path):
    from openpyxl import load_workbook
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook['Продажи'].iter_rows(values_only=True)
        headers = next(rows)
        indexes = [headers.index(x) for x in ('item_id', 'sku', 'item_name')]
        for row in rows:
            yield tuple(row[i] for i in indexes)
    finally:
        workbook.close()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('workbook')
    cli.add_argument('output', help='Private result path inside the ignored data directory')
    args = cli.parse_args()
    data_dir = Path('data').resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(data_dir) or output == data_dir:
        cli.error('output must be inside the local ignored data directory')
    started = datetime.now(timezone.utc).isoformat()
    req = Request(FEED_URL, headers={'User-Agent': 'Softline-local-audit/1.0', 'Accept-Encoding': 'identity'})
    with build_opener(FeedRedirectHandler()).open(req, timeout=60) as response:
        final_url = response.geturl()
        if urlsplit(final_url).scheme != 'https' or urlsplit(final_url).hostname != 'store.softline.ru':
            raise ValueError('Unexpected feed redirect')
        result = audit_stream(workbook_rows(args.workbook), response)
    result['provenance'] = {'requested_url': FEED_URL, 'response_url': final_url, 'retrieval_started_utc': started, 'retrieval_finished_utc': datetime.now(timezone.utc).isoformat(), 'limitation': 'Current feed, not a historical point-in-time snapshot. Name matches are exact trimmed strings only; all matches are candidates.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ('provenance', 'feed', 'denominator', 'summary')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
