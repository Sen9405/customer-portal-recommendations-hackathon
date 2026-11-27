import sqlite3


def test_status_snapshot_reads_latest_without_writing(tmp_path):
    path = tmp_path / 'review.sqlite'
    path.touch(mode=0o600)
    conn = sqlite3.connect(path)
    conn.executescript('CREATE TABLE cases(item_id TEXT); CREATE TABLE events(seq INTEGER, item_id TEXT, status TEXT, offer_id TEXT, reviewer TEXT, evidence_json TEXT, at_utc TEXT);')
    conn.execute('INSERT INTO cases VALUES (?)', ('private',))
    conn.execute('INSERT INTO events VALUES (1,?,?,?,?,?,?)', ('private', 'reviewed', '1', 'person', '{"source":"local"}', 'then'))
    conn.execute('INSERT INTO events VALUES (2,?,?,?,?,?,?)', ('private', 'rejected', None, 'person', '{"source":"local"}', 'later'))
    conn.commit(); conn.close()
    from catalog.offline_review_artifact import read_statuses
    status = read_statuses(path)['private']
    assert status['status'] == 'rejected' and status['at_utc'] == 'later'
    assert status['evidence'] == {'source': 'local'}
    conn = sqlite3.connect(path)
    assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 2
    conn.close()
