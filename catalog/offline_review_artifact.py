"""Create a private, static, offline identity-review worksheet (no decisions).

Run from project root: python -B -m catalog.offline_review_artifact
  data/identity-offline-comparison-top25.json data/identity-review.sqlite
  data/identity-review-worksheet.html

Open the resulting local HTML file in a browser. It contains private data. It has
no remote links, network access, forms, scripts or storage. It is a snapshot;
rerun with a NEW filename to refresh statuses. Record actual decisions separately
using catalog.identity_review; this tool never approves or modifies the database.
"""
import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path
import re
import sqlite3

from catalog.identity_review import _secure
from catalog.softline_audit import normalize_sku, url_sku

# Transparent, incomplete cues, not a product/edition ontology.
EDITION = re.compile(r'(?<!\w)(standard|professional|enterprise|essential|basic|advanced|premium|pro|стандарт|профессиональн\w*|корпоративн\w*|базов\w*)(?!\w)', re.I)


def classify(case):
    sources = case['workbook_rows_distinct_sku_name']
    offers = case['indexed_candidates']
    skus = [r.get('sku') for r in sources if isinstance(r.get('sku'), str) and r['sku']]
    names = [r.get('name') for r in sources if isinstance(r.get('name'), str) and r['name']]
    offer_names = [r.get('name') for r in offers if isinstance(r.get('name'), str) and r['name']]
    exact = any(s == o.get('vendor_code') for s in skus for o in offers)
    url = any(normalize_sku(s) and normalize_sku(s) == url_sku(o.get('url')) for s in skus for o in offers)
    normalized_code = any(normalize_sku(s) and normalize_sku(s) == normalize_sku(o.get('vendor_code')) for s in skus for o in offers)
    name_differs = bool(names and offer_names and any(a != b for a in names for b in offer_names))
    def cues(values):
        return {m.group().casefold() for value in values for m in EDITION.finditer(value)}
    source_cues, offer_cues = cues(names), cues(offer_names)
    f = case['findings']
    return {'raw_sku_exact': bool(exact), 'vendor_normalized': bool(normalized_code),
            'url_normalized': bool(url), 'name_differs': name_differs,
            'edition_signal_differs': bool((source_cues or offer_cues) and source_cues != offer_cues),
            'snapshot_conflict': f.get('snapshot_unique') is not True or f.get('snapshot_offer_id_agrees') is not True or f.get('offer_id_occurrences') != 1,
            'signal_disagreement': bool(skus and offers and (bool(normalized_code) != bool(url)))}


def read_statuses(path):
    _secure(path)
    conn = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + '?mode=ro', uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        rows = conn.execute('''SELECT c.item_id, e.status, e.offer_id, e.reviewer, e.evidence_json, e.at_utc
          FROM cases c LEFT JOIN events e ON e.seq=(SELECT MAX(x.seq) FROM events x WHERE x.item_id=c.item_id)''')
        return {item: {'status': status or 'pending', 'offer_id': offer, 'reviewer': reviewer,
                       'evidence': json.loads(evidence) if evidence else None, 'at_utc': at}
                for item, status, offer, reviewer, evidence, at in rows}
    finally:
        conn.close()


def render(cases, statuses, provenance):
    escape = lambda x: html.escape(str(x), quote=True)
    def block(value):
        return escape(json.dumps(value, ensure_ascii=False, indent=2))
    parts = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
             "<meta name='referrer' content='no-referrer'><title>Закрытая проверка соответствий</title>",
             "<style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:1.5em}section{border:1px solid #999;padding:1em;margin:1em 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f2f2;padding:1em}strong{color:#912}summary{cursor:pointer}li{margin:.3em}</style></head><body>",
             '<h1>Закрытая локальная проверка — 25 кандидатов</h1>',
             '<p><strong>Приватно.</strong> Не пересылать и не публиковать файл. Снимок статусов, а не интерфейс принятия решений. Открывайте только локально, без загрузки в облако. Никаких ссылок и сетевых запросов.</p>',
             '<p>Все сигналы — подсказки для проверки, не доказательство идентичности, лицензии, совместимости или наличия. URL и vendorCode сравниваются независимо; разные или отсутствующие сигналы не доказывают конфликт. Словарь редакций неполный. Фид текущий, не исторический.</p>',
             '<p>Для решения используйте отдельно <code>catalog.identity_review decide</code> с фактическим проверяющим и доказательствами. Этот файл ничего не утверждает и не меняет. Для обновления статуса создайте новый файл.</p>',
             '<details><summary>Происхождение снимка</summary><pre>' + block(provenance) + '</pre></details>']
    for c in cases:
        status = statuses.get(c['item_id'], {'status': 'missing_from_review_database'})
        signals = classify(c)
        parts.extend(['<section><h2>Приоритет ' + escape(c['rank']) + '</h2>',
                      '<p>Статус: <strong>' + escape(status['status']) + '</strong></p>',
                      '<h3>Сигналы сравнения</h3><pre>' + block(signals) + '</pre>',
                      '<h3>Данные и доказательства сравнения</h3><pre>' + block(c) + '</pre>',
                      '<h3>Последнее событие проверки</h3><pre>' + block(status) + '</pre></section>'])
    parts.append('</body></html>')
    return '\n'.join(parts)


def write_exclusive(path, content, root):
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    if not parent.is_relative_to(root.resolve(strict=True)) or path.name in ('', '.', '..'):
        raise ValueError('Output must be inside data/')
    target = parent / path.name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('comparison'); p.add_argument('database'); p.add_argument('output')
    args = p.parse_args()
    root = Path('data').resolve(strict=True)
    for value in (args.comparison, args.database):
        path = Path(value)
        if not path.resolve(strict=True).is_relative_to(root):
            p.error('Inputs must be inside data/')
        _secure(path)
    with open(args.comparison, encoding='utf-8') as f:
        data = json.load(f)
    cases = data['cases']
    if len(cases) != 25 or sorted(c['rank'] for c in cases) != list(range(1, 26)) or len({c['item_id'] for c in cases}) != 25:
        raise ValueError('Expected 25 distinct ranked cases')
    statuses = read_statuses(args.database)
    if any(c['item_id'] not in statuses for c in cases):
        raise ValueError('Comparison cases missing from review database')
    provenance = {k: v for k, v in data.items() if k != 'cases'}
    write_exclusive(args.output, render(cases, statuses, provenance), root)
    signals = Counter(k for c in cases for k, v in classify(c).items() if v)
    counts = Counter(statuses[c['item_id']]['status'] for c in cases)
    print(json.dumps({'cases': len(cases), 'signals_nonexclusive': dict(signals), 'statuses': dict(counts)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
