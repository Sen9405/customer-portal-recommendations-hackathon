"""Offline, private human identity review. Never treats candidates as proof.

CLI: python -m catalog.identity_review init data/softline-audit.json data/review.sqlite
     python -m catalog.identity_review counts data/review.sqlite
     python -m catalog.identity_review decide data/review.sqlite data/decision.json
Decision JSON contains item_id, status (reviewed/rejected), reviewer, evidence
({source, exact_excerpt, rationale}), and for reviewed, offer_id. Keep it private.
Only uniquely listed candidates can be exported as attestations; the audit caps
candidate lists at two, so ambiguous cases remain blocked even after review.
SQLite triggers enforce append-only events against ordinary SQL; file owners can
still alter/drop triggers or replace the database. This is not tamper-proof.
Neither review nor this module checks aliases, complete history or availability.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 20000:
        raise ValueError('Expected nonempty text (maximum 20000 characters)')
    return value


def _secure(path):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
        raise ValueError('Database must be a private owner-only regular file')


def connect(path):
    _secure(path)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def create(path, audit):
    if not isinstance(audit, dict) or not isinstance(audit.get('items'), dict):
        raise ValueError('Invalid audit')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.close(fd)
    try:
        conn = connect(path)
        try:
            conn.executescript('''PRAGMA journal_mode=DELETE;
              CREATE TABLE cases(item_id TEXT PRIMARY KEY, candidate_count INTEGER NOT NULL, evidence_json TEXT NOT NULL);
              CREATE TABLE events(seq INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES cases(item_id),
                status TEXT NOT NULL CHECK(status IN ('reviewed','rejected')), offer_id TEXT,
                reviewer TEXT NOT NULL, evidence_json TEXT NOT NULL, at_utc TEXT NOT NULL);
              CREATE TRIGGER no_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
              CREATE TRIGGER no_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
              CREATE TRIGGER no_case_update BEFORE UPDATE ON cases BEGIN SELECT RAISE(ABORT,'immutable'); END;
              CREATE TRIGGER no_case_delete BEFORE DELETE ON cases BEGIN SELECT RAISE(ABORT,'immutable'); END;''')
            conn.execute('BEGIN IMMEDIATE')
            for item, detail in audit['items'].items():
                _text(item)
                matches = detail['matches']['all_union']
                if not isinstance(matches, list) or len(matches) > 2 or any(not isinstance(c, dict) for c in matches):
                    raise ValueError('Invalid capped audit candidates')
                # Preserve all audit channels and provenance verbatim, not just candidate IDs.
                evidence = {'detail': detail, 'feed': audit.get('feed'), 'provenance': audit.get('provenance')}
                conn.execute('INSERT INTO cases VALUES (?,?,?)', (item, len(matches), json.dumps(evidence, ensure_ascii=False)))
            conn.execute('COMMIT')
        finally:
            conn.close()
    except BaseException:
        os.unlink(path)
        raise


def decide(path, decision):
    if not isinstance(decision, dict):
        raise ValueError('Invalid decision')
    item = _text(decision.get('item_id'))
    status = decision.get('status')
    if status not in ('reviewed', 'rejected'):
        raise ValueError('Decision must be reviewed or rejected')
    reviewer = _text(decision.get('reviewer'))
    evidence = decision.get('evidence')
    if not isinstance(evidence, dict):
        raise ValueError('Evidence required')
    for field in ('source', 'exact_excerpt', 'rationale'):
        _text(evidence.get(field))
    offer = decision.get('offer_id')
    if status == 'reviewed':
        _text(offer)
    elif offer is not None:
        raise ValueError('Rejected decisions cannot select an offer')
    conn = connect(path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT evidence_json FROM cases WHERE item_id=?', (item,)).fetchone()
        if row is None:
            raise ValueError('Unknown item')
        if status == 'reviewed':
            candidates = json.loads(row[0])['detail']['matches']['all_union']
            if offer not in [c.get('offer_id') for c in candidates]:
                raise ValueError('Offer must be a recorded candidate')
        conn.execute('INSERT INTO events(item_id,status,offer_id,reviewer,evidence_json,at_utc) VALUES (?,?,?,?,?,?)',
                     (item, status, offer, reviewer, json.dumps(evidence, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        conn.execute('COMMIT')
    except BaseException:
        if conn.in_transaction:
            conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()


def _latest(conn):
    return conn.execute('''SELECT c.item_id,c.candidate_count,c.evidence_json,e.status,e.offer_id
      FROM cases c LEFT JOIN events e ON e.seq=(SELECT MAX(x.seq) FROM events x WHERE x.item_id=c.item_id)''')


def counts(path):
    conn = connect(path)
    try:
        result = Counter()
        for _, n, _, status, _ in _latest(conn):
            result['total'] += 1
            result[status or 'pending'] += 1
            result[['unmatched', 'unique_candidate', 'ambiguous_or_capped'][n]] += 1
        return {k: result[k] for k in ('total','pending','reviewed','rejected','unmatched','unique_candidate','ambiguous_or_capped')}
    finally:
        conn.close()


def attestations(path):
    """Return reviewed unique candidate mappings privately, not availability proof.

    A caller must separately validate audit freshness, aliases and history; never
    use this as a historical snapshot. Rejection/re-review supersedes old events.
    """
    conn = connect(path)
    try:
        out = {}
        for item, n, raw, status, offer in _latest(conn):
            candidates = json.loads(raw)['detail']['matches']['all_union']
            if status == 'reviewed' and n == 1 and candidates[0].get('offer_id') == offer and isinstance(offer, str) and offer.strip():
                out[item] = offer
        return out
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init'); init.add_argument('audit'); init.add_argument('database')
    count = sub.add_parser('counts'); count.add_argument('database')
    dec = sub.add_parser('decide'); dec.add_argument('database'); dec.add_argument('decision')
    args = parser.parse_args()
    root = Path('data').resolve(strict=True)
    for name in ('database', 'audit', 'decision'):
        if hasattr(args, name):
            path = Path(getattr(args, name)).resolve()
            if not path.is_relative_to(root) or path == root:
                parser.error('All paths must be inside local data/')
            if name in ('audit', 'decision'):
                _secure(path)
    if args.command == 'init':
        with open(args.audit, encoding='utf-8') as f:
            create(args.database, json.load(f))
    elif args.command == 'decide':
        with open(args.decision, encoding='utf-8') as f:
            decide(args.database, json.load(f))
    print(json.dumps(counts(args.database)))


if __name__ == '__main__':
    main()
