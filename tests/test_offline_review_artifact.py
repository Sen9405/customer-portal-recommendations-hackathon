import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from catalog.offline_review_artifact import classify, render, write_exclusive


def case():
    return {'rank': 1, 'item_id': 'private', 'workbook_rows_distinct_sku_name': [{'sku': 'ABC', 'name': 'Product Standard'}],
            'audit_candidate': [{'offer_id': '1'}], 'indexed_candidates': [{'offer_id': '1', 'vendor_code': 'ABC', 'url': 'https://example/x', 'name': 'Product Professional', 'params_json': '[]'}],
            'findings': {'snapshot_unique': True, 'snapshot_offer_id_agrees': True, 'offer_id_occurrences': 1}, 'decision': 'pending', 'reviewer': ''}


def test_classification_and_disagreement():
    c = case()
    x = classify(c)
    assert x['raw_sku_exact'] and not x['url_normalized']
    assert x['name_differs'] and x['edition_signal_differs']
    c['indexed_candidates'][0]['vendor_code'] = 'other'
    c['indexed_candidates'][0]['url'] = 'https://example/abc-123/'
    x = classify(c)
    assert not x['raw_sku_exact'] and x['url_normalized']


def test_missing_values_and_conflicts():
    c = case()
    c['workbook_rows_distinct_sku_name'][0]['sku'] = ''
    c['indexed_candidates'][0]['vendor_code'] = ''
    c['indexed_candidates'][0]['url'] = 'https://example/'
    c['findings']['snapshot_offer_id_agrees'] = False
    x = classify(c)
    assert not x['raw_sku_exact'] and not x['url_normalized'] and x['snapshot_conflict']


def test_html_escapes_and_disables_network():
    c = case(); c['item_id'] = '<script>alert(1)</script>'
    html = render([c], {'private': {'status': 'pending'}}, {})
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert "default-src 'none'" in html
    assert '<a ' not in html and '<form' not in html
    assert 'pending' in html


def test_exclusive_private_output(tmp_path):
    path = tmp_path / 'review.html'
    write_exclusive(path, 'abc', tmp_path)
    assert path.read_text() == 'abc' and (path.stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError): write_exclusive(path, 'new', tmp_path)
    assert path.read_text() == 'abc'
    with pytest.raises(ValueError): write_exclusive(tmp_path.parent / 'escape.html', 'x', tmp_path)
    (tmp_path / 'link').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError): write_exclusive(tmp_path / 'link' / 'escape.html', 'x', tmp_path)
