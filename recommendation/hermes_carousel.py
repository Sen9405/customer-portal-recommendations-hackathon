"""Opt-in model ordering of the already selected snapshot candidates; not delivery."""
import json
import time
from datetime import date

from recommendation.hermes_candidate_review import (ReviewError, call_hermes, _LOCK,
    SUBJECT_CATEGORIES, SUBJECT_BASIS, SUBJECT_UNCERTAINTIES)
from recommendation.public_evidence_agent import _json_object


def run(profile, provider=None):
    if not _LOCK.acquire(blocking=False):
        raise ReviewError('busy')
    try:
        contest = profile.get('contest')
        if not contest or not contest.get('candidates'):
            raise ReviewError('no carousel candidates')
        candidates = contest['candidates']
        if len(candidates) > 20:
            raise ReviewError('too many candidates')
        offers = [c['offer_id'] for c in candidates]
        if any(type(x) is not str or not x for x in offers) or len(set(offers)) != len(offers):
            raise ReviewError('invalid offers')
        if any(c.get('snapshot_available') is not True for c in candidates):
            raise ReviewError('unavailable candidate')
        histories = profile['item_history']
        if len(histories) > 100:
            raise ReviewError('history too large')
        cutoff = date.fromisoformat(profile['as_of'])
        history = []
        for i, h in enumerate(histories):
            weeks = h['week_dates']
            if type(weeks) is not list or len(weeks) > 200 or any(date.fromisoformat(w) > cutoff for w in weeks):
                raise ReviewError('invalid history')
            history.append({'id': f'history-{i+1}', 'name': str(h['label']).rsplit(' [товар продаж: ', 1)[0][:300], 'week_dates': weeks, 'order_count': h['order_count']})
        history_map = {h['item_id']: history[i]['id'] for i, h in enumerate(histories)}
        if len(history_map) != len(histories):
            raise ReviewError('duplicate history')
        try:
            from recommendation.public_evidence_agent import evidence_for_offers
            source_evidence = evidence_for_offers(candidates)
        except (ImportError, OSError, ValueError, TypeError, KeyError):
            source_evidence = {}
        if type(source_evidence) is not dict:
            raise ReviewError('invalid sources')
        cs = []
        source_catalog = {}
        for i, c in enumerate(candidates):
            evidence = c.get('evidence', {})
            related = [history_map[x] for x in evidence.get('history_item_ids', []) if x in history_map]
            sources = source_evidence.get(c['offer_id'], [])
            if type(sources) is not list or len(sources) > 10:
                raise ReviewError('invalid sources')
            compact = []
            for source in sources:
                if any(type(source.get(k)) is not str or not source[k] for k in ('id', 'url', 'claim')) or not source['url'].startswith('https://'):
                    raise ReviewError('invalid source')
                compact.append({'id': source['id'][:200], 'url': source['url'][:1000],
                    'permitted_claim': source['claim'][:500],
                    'limitation': str(source.get('limitation') or 'No specific SKU entitlement or compatibility verified')[:500]})
            if len({x['id'] for x in compact}) != len(compact):
                raise ReviewError('duplicate source')
            for ref in compact:
                if ref['id'] in source_catalog and source_catalog[ref['id']] != ref:
                    raise ReviewError('conflicting source')
                source_catalog[ref['id']] = ref
            cs.append({'source_ids': [ref['id'] for ref in compact], 'id': f'candidate-{i+1}', 'name': str(c.get('name') or '')[:300],
                       'vendor': str(c.get('vendor') or '')[:150], 'source': c['source'],
                       'same_category': evidence.get('same_category') is True,
                       'same_vendor': evidence.get('same_vendor') is True, 'history_ids': related,
                       'method': evidence.get('method') if c['source'] == 'profile' else None})
        payload = {'_carousel_mode': True, 'cutoff_week': cutoff.isoformat(), 'history': history,
                   'sources': list(source_catalog.values()), 'candidates': cs}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 32768:
            raise ReviewError('input too large')
        start = time.monotonic()
        response = (provider or call_hermes)(payload)
        elapsed = time.monotonic() - start
        text, metrics = response if isinstance(response, tuple) else (response, {})
        obj = _json_object(text, {'order', 'assessments', 'source_ids'})
        ids = [c['id'] for c in cs]
        order = obj['order']
        if type(order) is not list or any(type(x) is not str for x in order) or len(order) != len(ids) or set(order) != set(ids):
            raise ReviewError('invalid order')
        if obj['source_ids'] != []:
            raise ReviewError('invalid sources')
        assessments = obj['assessments']
        if type(assessments) is not list or len(assessments) != len(ids):
            raise ReviewError('invalid assessments')
        allowed_history = {h['id'] for h in history}
        by_id = {}
        for a in assessments:
            if type(a) is not dict or set(a) != {'candidate_id', 'category', 'history_ids', 'basis', 'uncertainties', 'source_ids'}:
                raise ReviewError('invalid assessment')
            cid = a['candidate_id']
            if type(cid) is not str or cid not in ids or cid in by_id or a['category'] not in SUBJECT_CATEGORIES:
                raise ReviewError('invalid assessment id or category')
            for key, allowed in [('history_ids', allowed_history), ('basis', SUBJECT_BASIS), ('uncertainties', SUBJECT_UNCERTAINTIES)]:
                values = a[key]
                if type(values) is not list or any(type(v) is not str or v not in allowed for v in values) or len(values) != len(set(values)) or (key != 'history_ids' and not values):
                    raise ReviewError('invalid assessment evidence')
            available_sources = {x: source_catalog[x] for x in cs[ids.index(cid)]['source_ids']}
            selected = a['source_ids']
            if type(selected) is not list or any(type(x) is not str or x not in available_sources for x in selected) or len(selected) != len(set(selected)):
                raise ReviewError('invalid candidate source')
            # A relation without a referenced history item cannot be supported. Keep
            # the candidate but downgrade only this specific defect; all schema,
            # evidence and source checks above still apply without modification.
            category = a['category']
            if category in ('possible_variant', 'questionable_relation') and not a['history_ids']:
                category = 'uncertain'
            by_id[cid] = {**a, 'category': category, 'sources': [dict(available_sources[x]) for x in selected]}
        result = []
        for cid in order:
            index = ids.index(cid)
            result.append({'offer_id': offers[index], 'assessment': by_id[cid]})
        return {'status': 'model_ordered', 'candidates': result, 'model_seconds': round(elapsed, 3), 'token_usage': metrics,
                'source_ids': [], 'delivery': 'blocked'}
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ReviewError('invalid carousel review') from exc
    finally:
        _LOCK.release()
