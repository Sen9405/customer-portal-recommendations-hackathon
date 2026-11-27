"""Structural checks for small, manually verified public evidence records.

Validation does not verify the truth of external text or establish product identity.
"""
from datetime import date, datetime
from urllib.parse import urlsplit


def _timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('expected UTC timestamp')
    return datetime.fromisoformat(value[:-1] + '+00:00')


def validate_registry(registry):
    errors = []
    if not isinstance(registry, dict) or not isinstance(registry.get('relations'), list) or not registry['relations']:
        return ['relations must be a nonempty list']
    ids = set()
    hosts = {'support.kaspersky.com', 'helpcenter.veeam.com'}
    required = ('id', 'subject', 'object', 'relation_type', 'url', 'observed_at',
                'page_updated', 'source_version', 'excerpt', 'permitted_claim', 'caveats')
    for i, relation in enumerate(registry['relations']):
        if not isinstance(relation, dict):
            errors.append(f'{i}: expected object')
            continue
        for key in required:
            value = relation.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f'{i}: missing {key}')
        if relation.get('id') in ids:
            errors.append(f'{i}: duplicate id')
        ids.add(relation.get('id'))
        try:
            url = urlsplit(relation.get('url', ''))
            if url.scheme != 'https' or url.hostname not in hosts or url.username or url.password:
                raise ValueError('not a manufacturer URL')
        except (ValueError, TypeError):
            errors.append(f'{i}: invalid manufacturer URL')
        try:
            observed = _timestamp(relation.get('observed_at'))
            updated = date.fromisoformat(relation.get('page_updated', ''))
            if updated > observed.date():
                raise ValueError('updated after observation')
        except (ValueError, TypeError):
            errors.append(f'{i}: invalid dates')
        if relation.get('historical_snapshot_verified_at') is not None:
            errors.append(f'{i}: unsupported historical snapshot assertion')
    return errors


def permits_claim(relation, claim, version):
    """Only the exact manually approved claim for the exact source version is allowed."""
    return (not validate_registry({'relations': [relation]})
            and isinstance(claim, str) and claim == relation['permitted_claim']
            and isinstance(version, str) and version == relation['source_version'])


def usable_for_strict_history(relation, cutoff):
    """No archived, cutoff-contemporaneous evidence has been verified here.

    An update date alone cannot establish what content existed at a prior date.
    This registry intentionally cannot grant historical eligibility.
    """
    try:
        _timestamp(cutoff)
    except (ValueError, TypeError):
        return False
    return False
