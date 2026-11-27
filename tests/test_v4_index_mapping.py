import sqlite3

from catalog.v4_index_mapping import build


def test_distinct_occurrences_channels_and_duplicates():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE offers(row_id INTEGER, offer_id TEXT, vendor_code TEXT, url TEXT, name TEXT, available TEXT)')
    db.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?)', [
        (1, 'x', 'A-1', 'https://example.test/a-1-123', 'First', 'true'),
        (2, 'x', 'B', '', 'Second', 'false'),
        (3, 'z', 'B', '', 'Other', None),
        (4, 'y', None, '', 'Exact name', 'true'),
    ])
    rows = [('a', 'A1', 'irrelevant'), ('other', 'A1', ''), ('b', 'B', ''), ('N-test', '', 'Exact name'), ('none', '', '')]
    result = build(rows, db)
    assert result['denominator'] == {'items': 5, 'rows': 5}
    s = result['summary']
    assert (s['unique'], s['ambiguous'], s['no_candidate']) == (3, 1, 1)
    assert s['unique_duplicate_feed_offer_id'] == 2
    assert s['unique_items_shared_offer_id'] == 2
    assert result['feed']['duplicate_offer_id_values'] == 1
    assert result['feed']['rows_with_duplicate_offer_id'] == 2
    assert result['items']['a']['candidates'][0]['match_channels'] == ['vendorCode', 'url']
    assert result['items']['N-test']['candidates'][0]['match_channels'] == ['name_exact_N']
    assert s['unique_available_true'] == 3


def test_same_offer_id_different_rows_is_ambiguous():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE offers(row_id INTEGER, offer_id TEXT, vendor_code TEXT, url TEXT, name TEXT, available TEXT)')
    db.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?)', [(1,'x','a','','','true'), (2,'x','a','','','false')])
    r = build([('a','a','')], db)
    assert r['summary']['ambiguous'] == 1
    assert len(r['items']['a']['candidates']) == 2
