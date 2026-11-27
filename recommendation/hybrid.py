"""Local, fail-closed current-offer hybrid; never a historical backtest.

All output IDs are catalog offer IDs, never sales IDs. Candidate matches, even
unique ones, are NOT identities. `verified_identities` is an explicit upstream
human/system attestation {sales_id: offer_id}, not proof supplied by this module.
The caller must attest a complete purchase history, all history identities and
product aliases; unresolved/contradictory history blocks *all* recommendations.
The caller must also provide a live availability checker accepting an offer ID
and returning exactly True only when it is purchasable now. No checker, errors,
or any other result fail closed. Feed availability is an additional necessary
condition, not a real-time guarantee; availability can change after checking.
A feed ID must occur exactly once. Dedupe is by attested ID and known possible
matches, not by unobserved product aliases; equivalence across different offers,
complete histories and accuracy of attestations require upstream verification.
Do not pass private data to external checkers or publish individual results.
The synthetic demo uses no network or persistence. Current feed/attestations
are not valid point-in-time evidence for historical evaluation.
"""
from collections import Counter, defaultdict
import json
import math
import sqlite3

from catalog.catalog_index import query_candidates
from recommendation.provenance_links import build_graph, demo_inputs

MAX_HISTORY = 100
MAX_GRAPH_NEIGHBORS = 1000
MAX_CATALOG_ROWS_PER_HISTORY = 100
MAX_FALLBACK = 1000


class Hybrid:
    def __init__(self, model, connection, snapshot, registry, mappings, possible_matches=None,
                 *, verified_identities=None, availability_check=None):
        self.model = model
        self.conn = connection
        self.snapshot = snapshot
        self.edges = build_graph(snapshot, registry, mappings)
        self.possible = defaultdict(set)
        for item, offers in (possible_matches or {}).items():
            if not isinstance(item, str) or not item or not isinstance(offers, (list, set, tuple)) or any(not isinstance(x, str) or not x for x in offers):
                raise ValueError('invalid possible matches')
            self.possible[item].update(offers)
        for item, entry in snapshot['items'].items():
            offer = entry.get('candidate', {}).get('offer_id')
            if isinstance(item, str) and isinstance(offer, str) and offer:
                self.possible[item].add(offer)
        if verified_identities is None:
            verified_identities = {}
        if not isinstance(verified_identities, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in verified_identities.items()
        ):
            raise ValueError('invalid verified identities')
        if availability_check is not None and not callable(availability_check):
            raise ValueError('availability_check must be callable')
        self.verified = dict(verified_identities)
        self.availability_check = availability_check
        self.metadata = dict(connection.execute('SELECT key,value FROM metadata'))

    def recommend(self, history, limit=3, *, history_complete=False):
        if not isinstance(history, (list, tuple, set)) or len(history) > MAX_HISTORY or any(not isinstance(x, str) or not x for x in history):
            raise ValueError('history must contain at most 100 nonempty sales item ID strings')
        if type(limit) is not int or not 1 <= limit <= 3:
            raise ValueError('limit must be 1..3')
        if type(history_complete) is not bool:
            raise ValueError('history_complete must be a boolean')
        history = set(history)
        # A review can resolve ambiguity only if no supplied candidate contradicts it.
        def resolved(item):
            offer = self.verified.get(item)
            return bool(offer and not (self.possible[item] - {offer}))

        uncertain = sum(not resolved(item) for item in history)
        results = []
        fallback_used = False
        blocked_reason = None
        if not history_complete:
            blocked_reason = 'history_not_attested_complete'
        elif uncertain:
            blocked_reason = 'history_identity_unresolved'
        elif self.availability_check is None:
            blocked_reason = 'live_availability_unavailable'

        def offer_row(oid):
            rows = self.conn.execute('SELECT row_id,available FROM offers WHERE offer_id=? LIMIT 2', (oid,)).fetchall()
            return rows[0] if len(rows) == 1 else None

        # Even a reviewed history mapping cannot safely resolve a missing/duplicate
        # catalog ID: block rather than silently weakening exclusion.
        if not blocked_reason and any(offer_row(self.verified[item]) is None for item in history):
            blocked_reason = 'history_offer_missing_or_duplicate'
        selected_offers = {self.verified[item] for item in history if resolved(item)}
        for item in history:
            selected_offers.update(self.possible[item])

        def add_offer(oid, kind, evidence):
            if not oid or oid in selected_offers:
                return False
            row = offer_row(oid)
            if not row or row[1] != 'true':
                return False
            try:
                available = self.availability_check is not None and self.availability_check(oid) is True
            except Exception:
                available = False
            if not available:
                return False
            selected_offers.add(oid)
            results.append({'namespace': 'catalog_offer', 'id': oid, 'source': kind,
                            'availability': 'live_check_true_and_snapshot_true', 'evidence': evidence})
            return True

        def add_sales(item, kind, evidence):
            if item in history or not resolved(item):
                return False
            return add_offer(self.verified[item], kind, evidence)

        if not blocked_reason:
            scores = Counter()
            for item in sorted(history):
                neighbors = self.model['pairs'].get(item, {})
                for target, count in sorted(neighbors.items(), key=lambda x: (-x[1], str(x[0])))[:MAX_GRAPH_NEIGHBORS]:
                    if target not in history:
                        scores[target] += count / math.sqrt(self.model['degree'][item] * self.model['degree'][target])
            for target in sorted(scores, key=lambda x: (-scores[x], str(x))):
                if len(results) >= limit:
                    break
                add_sales(target, 'transaction_graph', {'score': scores[target], 'meaning': 'co-order association, not compatibility or causality', 'order_id_caveat': 'order IDs may recur across weeks'})

            for edge in self.edges:
                if len(results) >= limit:
                    break
                subject, obj, relation = edge['subject_mapping'], edge['object_mapping'], edge['relation']
                if subject['item_id'] in history:
                    add_sales(obj['item_id'], 'reviewed_family_relation', {'relation': relation, 'limitations': edge['limitations']})

            for source in sorted(history):
                if len(results) >= limit:
                    break
                offer = self.verified[source]
                row = self.conn.execute('SELECT category_id FROM offers WHERE offer_id=?', (offer,)).fetchone()
                if not row or not row[0]:
                    continue
                for candidate in query_candidates(self.conn, category_id=row[0], limit=MAX_CATALOG_ROWS_PER_HISTORY):
                    if len(results) >= limit:
                        break
                    add_offer(candidate['offer_id'], 'catalog_similarity', {'basis': 'same category; similarity only, not compatibility'})

            for target in self.model['ranking'][:MAX_FALLBACK]:
                if len(results) >= limit:
                    break
                fallback_used |= add_sales(target, 'popularity_fallback', {'meaning': 'training row-count popularity; not compatibility', 'count': self.model['counts'][target]})

        return {'recommendations': results, 'fallback_used': fallback_used,
                'blocked_reason': blocked_reason, 'uncertain_history_count': uncertain,
                'catalog_provenance': self.metadata,
                'limitations': ['Current catalog and current source evidence are not valid for a leakage-free historical backtest.',
                                'Safety depends on complete history, identity and alias attestations supplied by the caller.',
                                'Live availability is subject to a race and requires rechecking at purchase.',
                                'B2B industry and segment are not used; query bounds may leave slots empty.']}


def possible_from_audit(audit):
    """Include every known match candidate, including ambiguous items."""
    return {str(item): [x['offer_id'] for x in detail['matches']['all_union'] if x.get('offer_id')]
            for item, detail in audit['items'].items()}


def main():
    from recommendation.experimental_graph import fit
    snapshot, registry, mappings = demo_inputs()
    conn = sqlite3.connect(':memory:')
    conn.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);
      CREATE TABLE offers(row_id INTEGER PRIMARY KEY,offer_id TEXT,name TEXT,vendor TEXT,vendor_code TEXT,category_id TEXT,url TEXT,available TEXT,params_json TEXT);
      CREATE INDEX offers_id ON offers(offer_id);
      CREATE INDEX offers_category_row ON offers(category_id,row_id);''')
    conn.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?)', [
        (1, 'offer-a', 'Synthetic A', 'Synthetic', None, 'cat', None, 'true', '[]'),
        (2, 'offer-b', 'Synthetic B', 'Synthetic', None, 'cat', None, 'true', '[]'),
        (3, 'offer-c', 'Synthetic C', 'Synthetic', None, 'cat', None, 'true', '[]')])
    model = fit([('order', 'user', '2026-01-01', 'item-a'), ('order', 'user', '2026-01-01', 'item-c')])
    result = Hybrid(model, conn, snapshot, registry, mappings,
                    verified_identities={'item-a': 'offer-a', 'item-b': 'offer-b', 'item-c': 'offer-c'},
                    availability_check=lambda _: True).recommend(['item-a'], history_complete=True)
    print(json.dumps({'synthetic_only': True, **result}, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == '__main__':
    main()
