"""Local deterministic review; no model, network, logging or client delivery.

Registry digest pins checked-in public evidence, not a signature against an attacker
able to change code. Registry entries have no verified SKU links. Never infer links
from names, provider text, catalog matches, or untrusted snapshots.
"""
import hashlib
import json
from pathlib import Path

from recommendation.public_evidence_agent import REGISTRY, TOPICS, _validate_record, _json_object

REGISTRY_SHA256 = 'bc2a3675b4bef97226a0fb9c089e7b55c73f77ffc747074dbb7181084084e81a'


def registry_status():
    try:
        raw = Path(REGISTRY).read_bytes()
        if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
            raise ValueError('digest mismatch')
        obj = _json_object(raw.decode('utf-8'), {'schema_version', 'scope', 'relations'})
        if obj['schema_version'] != 1 or type(obj['relations']) is not list:
            raise ValueError('invalid registry')
        records = obj['relations']
        ids = [r['id'] for r in records]
        if len(ids) != len(set(ids)) or set(ids) != {x for topic in TOPICS.values() for x in topic}:
            raise ValueError('invalid identities')
        for record in records:
            _validate_record(record)
        return 'verified_unlinked'
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        return 'unavailable_or_modified'


def review(profile):
    """Consume local cutoff-limited profile only; returns blocked diagnostics."""
    state = registry_status()
    candidates = []
    for c in profile['candidates']:
        ev = c['evidence']
        candidates.append({
            'item_id': c['item_id'],
            'basis': {'method': c['method'], 'coorder': ev['coorder'],
                      'sequential': ev['sequential'],
                      'train_unique_order_count': ev['train_unique_order_count']},
            'sources': [],
            'registry_status': state,
            'unknowns': ['identity_and_aliases', 'history_completeness', 'sku_relation',
                         'compatibility_and_entitlement', 'live_availability',
                         'point_in_time_registry_and_catalog'],
            'decision': 'blocked',
        })
    # These are registry-level references only, never evidence for any candidate.
    references = ([{'id': 'kaspersky-ksc14-endpoint-windows', 'url': 'https://support.kaspersky.com/ksc/14.0/172903'},
                   {'id': 'veeam-one13-backup-monitoring', 'url': 'https://helpcenter.veeam.com/docs/one/userguide/backup_monitoring.html?ver=13'},
                   {'id': 'veeam-backup13-vsphere', 'url': 'https://helpcenter.veeam.com/docs/vbr/userguide/vmware_vsphere.html?ver=13'}]
                  if state == 'verified_unlinked' else [])
    return {'mode': 'local_deterministic_no_llm', 'status': 'blocked',
            'registry_references_unlinked': references,
            'as_of': profile['as_of'], 'candidates': candidates, 'recommendations': [],
            'protocol': ['train_week_labels_lte_cutoff', 'test_targets_excluded',
                         'undated_attributes_and_current_catalog_excluded_from_ranking',
                         'week_label_not_exact_purchase_time',
                         'registry_has_no_attested_sku_links',
                         'current_registry_not_historical_evidence'],
            'registry_status': state}
