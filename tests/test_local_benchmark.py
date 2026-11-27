import socket
import sqlite3

import pytest

from recommendation.local_benchmark import benchmark, percentile


def test_nearest_rank():
    assert percentile(list(range(1, 101)), .5) == 50
    assert percentile(list(range(1, 101)), .95) == 95
    with pytest.raises(ValueError):
        percentile([], .5)


def test_benchmark_aggregate_and_read_only(tmp_path):
    path = tmp_path / 'catalog.sqlite'
    conn = sqlite3.connect(path)
    conn.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);
        CREATE TABLE offers(row_id INTEGER PRIMARY KEY,offer_id TEXT,name TEXT,vendor TEXT,vendor_code TEXT,category_id TEXT,url TEXT,available TEXT,params_json TEXT);
        CREATE INDEX offers_id ON offers(offer_id);
        CREATE INDEX offers_category_row ON offers(category_id,row_id);''')
    conn.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?)', [
        (i, f'private-{i}', 'secret-name', '', '', 'cat', '', 'true', '[]') for i in range(1, 6)])
    conn.commit()
    conn.close()
    before = path.read_bytes()
    original_socket = socket.socket
    result = benchmark(path, 2, 5)
    assert socket.socket is original_socket
    assert path.read_bytes() == before
    assert result['configuration']['catalog_offer_rows'] == 5
    assert result['scenarios']['catalog_path']['count'] == 5
    assert result['scenarios']['catalog_path']['recommendation_slots'] == 15
    assert result['scenarios']['blocked_incomplete']['blocked_requests'] == 5
    assert 'secret-name' not in str(result) and 'private-' not in str(result)
    with pytest.raises(ValueError):
        benchmark(path, 0, 0)
