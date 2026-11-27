"""Current public Softline catalog, not a historical snapshot or compatibility proof.

Build: python3 -m catalog.catalog_index data/catalog-YYYYMMDD.sqlite
Query via query_candidates(connection, category_id=..., vendor=..., after=...).
Rows are distinct feed occurrences; duplicate offer IDs are never merged.
No workbook or private sales data is read or sent over the network.
"""
import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from xml.parsers import expat

from catalog.softline_audit import FEED_URL, FeedRedirectHandler


def create_index(path):
    """Exclusively reserve a private database; never overwrite an existing file."""
    path = Path(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.executescript('''
          PRAGMA temp_store=FILE;
          CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE categories (category_id TEXT, parent_id TEXT, name TEXT);
          CREATE TABLE offers (row_id INTEGER PRIMARY KEY, offer_id TEXT, name TEXT,
            vendor TEXT, vendor_code TEXT, category_id TEXT, url TEXT, available TEXT,
            params_json TEXT NOT NULL);
        ''')
        return conn
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def ingest_stream(conn, compressed, retrieved_at, *, max_xml_bytes=1024 * 1024 * 1024):
    """Parse gzip with hard limits and no entities; retain only one offer at a time."""
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    def reject(*_):
        raise ValueError('XML entities forbidden')
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    depth = 0
    offer = None
    capture = None
    capture_depth = None
    chunks = []
    length = 0
    attrs_saved = None
    count = 0
    feed_date = None
    fields = {'name', 'vendor', 'vendorCode', 'categoryId', 'url', 'param'}

    def start(tag, attrs):
        nonlocal depth, offer, capture, capture_depth, chunks, length, attrs_saved, feed_date
        depth += 1
        if depth > 64 or sum(len(k) + len(v) for k, v in attrs.items()) > 65536:
            raise ValueError('XML depth or attributes limit exceeded')
        if tag == 'yml_catalog':
            feed_date = attrs.get('date')
        if tag == 'offer':
            if offer is not None:
                raise ValueError('Nested offer')
            offer = {'id': attrs.get('id'), 'available': attrs.get('available'), 'params': [], '_depth': depth}
        elif capture is None and ((offer is not None and depth == offer['_depth'] + 1 and tag in fields) or (offer is None and tag == 'category')):
            capture, capture_depth, chunks, length, attrs_saved = tag, depth, [], 0, dict(attrs)

    def text(value):
        nonlocal length
        if capture is not None:
            length += len(value)
            if length > 65536:
                raise ValueError('XML field size limit exceeded')
            chunks.append(value)

    def end(tag):
        nonlocal depth, offer, capture, capture_depth, chunks, count
        if capture == tag and capture_depth == depth:
            value = ''.join(chunks).strip()
            if tag == 'category':
                conn.execute('INSERT INTO categories VALUES (?,?,?)', (attrs_saved.get('id'), attrs_saved.get('parentId'), value))
            elif tag == 'param':
                if len(offer['params']) >= 512:
                    raise ValueError('Offer parameter limit exceeded')
                offer['params'].append({'name': attrs_saved.get('name'), 'unit': attrs_saved.get('unit'), 'value': value})
            else:
                offer[tag] = value
            capture, capture_depth, chunks = None, None, []
        if tag == 'offer' and offer is not None and depth == offer['_depth']:
            count += 1
            conn.execute('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?)',
                         (count, offer['id'], offer.get('name'), offer.get('vendor'), offer.get('vendorCode'),
                          offer.get('categoryId'), offer.get('url'), offer['available'],
                          json.dumps(offer['params'], ensure_ascii=False)))
            offer = None
        depth -= 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    total = 0
    with conn:
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
        if not count:
            raise ValueError('Feed contains no offers')
        conn.executemany('INSERT INTO metadata VALUES (?,?)', [
            ('retrieval_started_utc', retrieved_at), ('feed_date_as_supplied', feed_date),
            ('xml_bytes', str(total)), ('offer_count', str(count)),
            ('limitation', 'Current feed only; not historical. Similarity is not compatibility.')])
        conn.execute('CREATE INDEX offers_category_row ON offers(category_id, row_id)')
        conn.execute('CREATE INDEX offers_vendor_row ON offers(vendor, row_id)')
        conn.execute('CREATE INDEX offers_category_vendor_row ON offers(category_id, vendor, row_id)')
        conn.execute('CREATE INDEX offers_id ON offers(offer_id)')
        conn.execute('CREATE INDEX categories_id ON categories(category_id)')
    return {'offers': count, 'xml_bytes': total, 'feed_date_as_supplied': feed_date}


def query_candidates(conn, *, category_id=None, vendor=None, after=0, limit=100):
    """Exact category/vendor filter, ascending occurrence cursor; raw availability preserved.

    At least one filter is required; after is the row_id of the previous page's last row.
    Feed text is untrusted data. Offers are candidates, not verified product matches.
    """
    if not category_id and not vendor:
        raise ValueError('category_id or vendor required')
    if type(limit) is not int or not 1 <= limit <= 1000 or type(after) is not int or after < 0:
        raise ValueError('invalid pagination')
    clauses, values = ['row_id > ?'], [after]
    if category_id is not None:
        clauses.append('category_id = ?')
        values.append(category_id)
    if vendor is not None:
        clauses.append('vendor = ?')
        values.append(vendor)
    values.append(limit)
    rows = conn.execute('SELECT row_id,offer_id,name,vendor,vendor_code,category_id,url,available,params_json FROM offers WHERE '
                        + ' AND '.join(clauses) + ' ORDER BY row_id LIMIT ?', values)
    keys = ('row_id', 'offer_id', 'name', 'vendor', 'vendor_code', 'category_id', 'url', 'available', 'params')
    result = []
    for row in rows:
        item = dict(zip(keys, row))
        item['params'] = json.loads(item['params'])
        result.append(item)
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output', help='New SQLite file inside ignored data/')
    args = cli.parse_args()
    root = Path('data').resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(root) or output == root:
        cli.error('output must be inside data/')
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = create_index(output)
    try:
        started = datetime.now(timezone.utc).isoformat()
        request = Request(FEED_URL, headers={'User-Agent': 'Softline-catalog-index/1.0', 'Accept-Encoding': 'identity'})
        with build_opener(FeedRedirectHandler()).open(request, timeout=60) as response:
            target = urlsplit(response.geturl())
            if target.scheme != 'https' or target.hostname != 'store.softline.ru' or target.port not in (None, 443):
                raise ValueError('Unexpected feed redirect')
            stats = ingest_stream(conn, response, started)
            provenance = {'requested_url': FEED_URL, 'response_url': response.geturl(),
                          'retrieval_finished_utc': datetime.now(timezone.utc).isoformat(),
                          'last_modified': response.headers.get('Last-Modified'), 'etag': response.headers.get('ETag')}
            with conn:
                conn.executemany('INSERT INTO metadata VALUES (?,?)', provenance.items())
        print(json.dumps({**stats, **provenance}, ensure_ascii=False))
    except BaseException:
        conn.close()
        output.unlink(missing_ok=True)
        raise
    conn.close()


if __name__ == '__main__':
    main()
