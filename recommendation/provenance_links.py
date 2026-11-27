"""Offline reviewed offer-to-family provenance links; run synthetic demo with
`python -B -m recommendation.provenance_links`.

A mapping is a human attestation, not machine verification of its evidence.
No SKU compatibility, entitlement, availability, historical applicability or
recommendation is inferred. Never send snapshot or mapping data to a network.
The digests bind reviews to the complete snapshot and exact relation record.
"""
from datetime import datetime
import hashlib
import json
from urllib.parse import urlsplit

from sources.registry import validate_registry


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError('missing or invalid ' + key)
    return value


def _timestamp(value):
    if not value.endswith('Z'):
        raise ValueError('expected UTC timestamp')
    datetime.fromisoformat(value[:-1] + '+00:00')


def build_graph(snapshot, registry, mappings, *, cutoff=None):
    """Return only paired, explicitly reviewed directional relation links.

    Each mapping must bind relation_id + side (subject/object) to an exact item,
    offer_id and SKU with evidence, reviewer and edition caveats. An explicit SKU
    is an attested manufacturer SKU, not a value inferred from the feed. Incomplete,
    stale, ambiguous or duplicate reviews reject the entire operation. A lone
    reviewed side emits no edge. History is unsupported and rejected outright.
    """
    if cutoff is not None:
        raise ValueError('historical eligibility is not established')
    if validate_registry(registry):
        raise ValueError('invalid public relation registry')
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('items'), dict):
        raise ValueError('invalid catalog snapshot')
    if not isinstance(mappings, list):
        raise ValueError('mappings must be a list')
    relations = {r['id']: r for r in registry['relations']}
    snapshot_hash = digest(snapshot)
    offer_counts = {}
    for entry in snapshot['items'].values():
        if isinstance(entry, dict) and isinstance(entry.get('candidate'), dict):
            offer = entry['candidate'].get('offer_id')
            if isinstance(offer, str) and offer.strip():
                offer_counts[offer] = offer_counts.get(offer, 0) + 1
    reviewed = {}
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise ValueError('mapping must be an object')
        for key in ('relation_id', 'side', 'item_id', 'offer_id', 'sku', 'family',
                    'status', 'reviewed_by', 'reviewed_at', 'evidence_url',
                    'evidence_locator', 'evidence_excerpt', 'edition_caveats',
                    'snapshot_sha256', 'relation_sha256'):
            _text(mapping, key)
        relation = relations.get(mapping['relation_id'])
        side = mapping['side']
        if not relation or side not in ('subject', 'object') or mapping['family'] != relation[side]:
            raise ValueError('unknown relation, side or family')
        if mapping['status'] != 'verified' or mapping['snapshot_sha256'] != snapshot_hash or mapping['relation_sha256'] != digest(relation):
            raise ValueError('unreviewed or stale mapping')
        _timestamp(mapping['reviewed_at'])
        if mapping['sku'] not in mapping['evidence_excerpt']:
            raise ValueError('evidence excerpt must explicitly mention the SKU')
        try:
            url = urlsplit(mapping['evidence_url'])
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443):
                raise ValueError('invalid evidence URL')
        except ValueError as exc:
            raise ValueError('invalid evidence URL') from exc
        item = snapshot['items'].get(mapping['item_id'])
        if not isinstance(item, dict) or not isinstance(item.get('candidate'), dict):
            raise ValueError('unknown catalog item')
        if item['candidate'].get('offer_id') != mapping['offer_id'] or offer_counts.get(mapping['offer_id']) != 1:
            raise ValueError('offer identity missing or ambiguous')
        key = (mapping['relation_id'], side, mapping['item_id'])
        if key in reviewed:
            raise ValueError('duplicate mapping')
        reviewed[key] = dict(mapping)
    graph = []
    for relation_id, relation in relations.items():
        subjects = [m for (r, side, _), m in reviewed.items() if r == relation_id and side == 'subject']
        objects = [m for (r, side, _), m in reviewed.items() if r == relation_id and side == 'object']
        for subject in subjects:
            for obj in objects:
                if subject['item_id'] == obj['item_id'] or subject['offer_id'] == obj['offer_id']:
                    raise ValueError('same offer cannot represent both sides')
                graph.append({'subject_mapping': subject, 'object_mapping': obj,
                              'relation': dict(relation),
                              'limitations': 'Family relation only; not compatibility, entitlement, availability, or historical proof. Human evidence review required.'})
    return graph


def demo_inputs():
    """Entirely synthetic example; example evidence URLs are deliberately non-real."""
    snapshot = {'schema_version': 1, 'items': {
        'item-a': {'candidate': {'offer_id': 'offer-a'}},
        'item-b': {'candidate': {'offer_id': 'offer-b'}}}}
    # Registry validator allows only existing manufacturer hosts; this record is
    # fabricated and is never presented as a real manufacturer claim.
    relation = {'id': 'synthetic', 'subject': 'Synthetic Family A', 'object': 'Synthetic Family B',
                'relation_type': 'synthetic', 'url': 'https://support.kaspersky.com/synthetic-example',
                'observed_at': '2026-01-02T00:00:00Z', 'page_updated': '2026-01-01',
                'source_version': 'synthetic version', 'excerpt': 'Synthetic excerpt.',
                'permitted_claim': 'Synthetic family-level relation only.',
                'caveats': 'All data in this demonstration is invented.', 'historical_snapshot_verified_at': None}
    registry = {'relations': [relation]}
    common = {'relation_id': 'synthetic', 'status': 'verified', 'reviewed_by': 'synthetic reviewer',
              'reviewed_at': '2026-01-02T00:00:00Z', 'evidence_url': 'https://example.org/synthetic',
              'evidence_locator': 'synthetic page 1', 'evidence_excerpt': 'Synthetic SKU identity evidence.',
              'edition_caveats': 'Synthetic edition only; no other edition established.',
              'snapshot_sha256': digest(snapshot), 'relation_sha256': digest(relation)}
    mappings = [dict(common, side='subject', item_id='item-a', offer_id='offer-a', sku='SKU-A', family=relation['subject'], evidence_excerpt='Synthetic SKU-A identity evidence.'),
                dict(common, side='object', item_id='item-b', offer_id='offer-b', sku='SKU-B', family=relation['object'], evidence_excerpt='Synthetic SKU-B identity evidence.')]
    return snapshot, registry, mappings


if __name__ == '__main__':
    print(json.dumps({'synthetic_only': True, 'edges': build_graph(*demo_inputs())}, indent=2))
