"""Bounded, opt-in catalog investigation around the unchanged main carousel.

Two calls to the existing Hermes model bridge: first it orders candidate search
paths, then it evaluates discovered offers. Catalog tools are local, explicitly
allowlisted lookups; model output cannot choose arbitrary paths, SQL or URLs.
This is a research prototype, not verified compatibility or automatic delivery.
"""
import re
from difflib import SequenceMatcher

from recommendation.hermes_carousel import run as model_rank
from recommendation.public_evidence_agent import evidence_for_offers

MAX_DIRECTIONS = 12
MAX_POOL = 20
MAX_FINDINGS = 3
MAX_SCAN_PER_PATH = 2000
MAX_FALLBACK_CATALOG = 150000
MAX_FALLBACK_HISTORY = 12
MAX_NAME = 512


# Generic sales and licensing vocabulary must not turn a vendor match into an
# apparent product match. This is a deliberately conservative lexical hint.
_GENERIC = frozenset('license licence лицензия лицензии лицензий лицензиям лицензийное '
    'продление renewal renewallicence subscription подписка standard advanced '
    'business professional product software security защита комплект пакет '
    'год года лет year years month месяцев рабочих мест пользователь пользователей '
    'для от до на и the with for windows сервер server workstation'.split())


def _product_words(name, vendor):
    def tokens(value):
        return set(re.findall(r'[^\W_]+', str(value or '').casefold(), flags=re.UNICODE))
    return {t for t in tokens(name) - tokens(vendor) - _GENERIC
            if len(t) >= 3 and not t.isdigit()}


def _as_customer_question(candidate, assessment):
    """Keep the model assessment, but do not infer a customer task from it."""
    return {**candidate, 'assessment': assessment,
            'grounding_status': 'customer_task_unknown'}


def _fallback_paths(history, catalog, excluded):
    """Local, bounded lexical hints only; never assert an identity or relation."""
    names = []
    for h in history[:MAX_FALLBACK_HISTORY]:
        label = h.get('label')
        if not isinstance(label, str) or len(label) > MAX_NAME:
            continue
        label = label.rsplit(' [товар продаж: ', 1)[0]
        words = _product_words(label, '')
        if 2 <= len(words) <= 40:
            names.append((h['item_id'], label.casefold(), words))
    if not names:
        return []
    # An oversized index is not partially scanned: prefix sampling would make
    # the arbitrary offer-ID order determine whether evidence exists.
    if len(catalog.by_id) > MAX_FALLBACK_CATALOG:
        return []
    matches = {str(item): [] for item, _, _ in names}
    for oid in sorted(catalog.by_id, key=str):
        obj = catalog.by_id[oid]
        if not obj or str(oid) in excluded or obj.get('availability') != 'true':
            continue
        name = obj.get('name')
        if not isinstance(name, str) or len(name) > MAX_NAME:
            continue
        words = _product_words(name, '')
        relevant = [(item, label, hw) for item, label, hw in names
                    if len(hw & words) >= 2]
        if not relevant:
            continue
        if any(SequenceMatcher(None, label, name.casefold(), autojunk=False).ratio() >= .8
               for _, label, _ in names):
            continue
        for item, label, history_words in relevant:
            if len(matches[str(item)]) < MAX_SCAN_PER_PATH:
                matches[str(item)].append(obj)
    return [{'kind': 'name_hint', 'value': '', 'history_item': item,
             'entries': sorted(matches[str(item)], key=lambda x: str(x['offer_id']))[:MAX_SCAN_PER_PATH]}
            for item, _, _ in names if matches[str(item)]][:MAX_DIRECTIONS]


def explore(profile, catalog, ranker=model_rank, *, allow_name_fallback=False):
    """Explore a validated ContestRecommender catalog using a model-selected path.

    `ranker` follows hermes_carousel.run; injectable for deterministic tests only.
    Existing output stays separate. Every observed offer mapping is excluded even
    if ambiguous. Missing SKU identity means duplicates remain possible.
    """
    history = profile.get('item_history', [])
    if not history or not profile.get('contest'):
        raise ValueError('history or carousel missing')
    seen_ids = {str(h['item_id']): h['item_id'] for h in history}
    seen = set(seen_ids)
    excluded = {str(c['offer_id']) for item in seen
                for c in catalog.items.get(item, {}).get('candidates', [])
                if c.get('offer_id') is not None}
    excluded.update(str(c['offer_id']) for c in profile['contest'].get('candidates', []))
    directions = []
    used = set()
    for item in sorted(seen):
        source = catalog._unique(item)
        if not source:
            continue
        for kind, raw in [('category', source.get('category_id')), ('vendor', source.get('vendor'))]:
            value = str(raw).strip() if raw else ''
            key = (kind, value.casefold())
            if not value or key in used:
                continue
            used.add(key)
            directions.append({'kind': kind, 'value': value, 'history_item': seen_ids[item]})
            if len(directions) >= MAX_DIRECTIONS:
                break
        if len(directions) >= MAX_DIRECTIONS:
            break
    # The existing UI labels every non-category action as a vendor; do not
    # expose name paths there until it can represent their provenance correctly.
    if not directions and allow_name_fallback:
        directions = _fallback_paths(history, catalog, excluded)
    if not directions:
        return {'status': 'no_paths', 'actions': [], 'findings': [], 'delivery': 'blocked',
                'limitations': ['История не сопоставлена с каталогом однозначно.']}
    # Registry lookup only: no history-derived search or network requests. The
    # history's *unverified* mapping is a context hint, not SKU identification.
    history_offers = []
    history_offer_by_item = {}
    for item in sorted(seen):
        source = catalog._unique(item)
        if source and source.get('offer_id') is not None:
            history_offers.append({'offer_id': str(source['offer_id']),
                                   'name': source.get('name') or ''})
            history_offer_by_item[str(item)] = str(source['offer_id'])
    try:
        history_refs = evidence_for_offers(history_offers)
    except (OSError, ValueError, TypeError, KeyError):
        history_refs = {}

    synthetic = []
    for i, d in enumerate(directions):
        synthetic.append({'offer_id': 'path-' + str(i), 'snapshot_available': True,
            'name': (('Категория ' if d['kind'] == 'category' else 'Производитель ') + d['value']
                     if d['kind'] != 'name_hint' else 'Локальное совпадение слов названия'),
            'vendor': '', 'source': 'related_feed',
            'evidence': {'history_item_ids': [d['history_item']]}})
    request = {**profile, 'contest': {'candidates': synthetic}}
    path_result = ranker(request)
    path_ids = [x['offer_id'] for x in path_result['candidates']]
    expected = {x['offer_id'] for x in synthetic}
    if len(path_ids) != len(expected) or set(path_ids) != expected:
        raise ValueError('invalid model path order')
    actions, pool, present = [], [], set()
    # The model chooses the first two paths; no arbitrary free-text search.
    for identifier in path_ids[:2]:
        d = directions[int(identifier.split('-')[1])]
        history_source = catalog._unique(str(d['history_item'])) or {}
        if d['kind'] == 'name_hint':
            # There is no unique catalog source on this path: use the actual
            # train label that generated the hint, not an empty mapped name.
            label = next((h.get('label', '') for h in history
                          if str(h['item_id']) == str(d['history_item'])), '')
            history_words = _product_words(label.rsplit(' [товар продаж: ', 1)[0], '')
        else:
            history_words = _product_words(history_source.get('name'), history_source.get('vendor'))
        history_source_ids = {ref['id'] for ref in history_refs.get(
            history_offer_by_item.get(str(d['history_item']), ''), [])}
        entries = (d['entries'] if d['kind'] == 'name_hint' else
                   catalog.by_category.get(d['value'], []) if d['kind'] == 'category'
                   else catalog.by_vendor.get(d['value'].casefold(), []))
        added = 0
        # Deterministic order and bounded returned items, regardless of feed ordering.
        eligible = []
        for obj in sorted(entries, key=lambda x: str(x['offer_id'])):
            oid = str(obj['offer_id'])
            if oid in excluded or oid in present or catalog.by_id.get(oid) is not obj:
                continue
            if obj.get('availability') != 'true':
                continue
            eligible.append(obj)
            if len(eligible) >= MAX_SCAN_PER_PATH:
                break
        # Prefer a shared reviewed document, then distinctive product-name words;
        # neither is proof of a relation. Keep a deterministic broad fallback.
        try:
            refs = evidence_for_offers([{'offer_id': str(o['offer_id']),
                                         'name': o.get('name') or ''} for o in eligible])
        except (OSError, ValueError, TypeError, KeyError):
            refs = {}
        def shared(o):
            return bool(history_source_ids.intersection(
                r['id'] for r in refs.get(str(o['offer_id']), [])))
        eligible.sort(key=lambda o: (
            not shared(o),
            -len(history_words & _product_words(o.get('name'), o.get('vendor'))),
            str(o['offer_id']),
        ))
        for obj in eligible:
            oid = str(obj['offer_id'])
            present.add(oid)
            pool.append({'offer_id': oid, 'name': obj.get('name') or '',
                'vendor': obj.get('vendor') or '', 'category_id': obj.get('category_id'),
                'url': obj.get('url'), 'snapshot_available': True, 'source': 'related_feed',
                'evidence': {'same_category': d['kind'] == 'category',
                             'same_vendor': d['kind'] == 'vendor',
                             'name_hint': d['kind'] == 'name_hint',
                             'history_item_ids': [d['history_item']]}})
            added += 1
            if len(pool) >= MAX_POOL or added >= MAX_POOL // 2:
                break
        actions.append({'tool': 'catalog_' + d['kind'], 'query': d['value'],
                        'returned': added, 'bounded': added >= MAX_POOL // 2 or len(eligible) >= MAX_SCAN_PER_PATH})
        if len(pool) >= MAX_POOL:
            break
    if not pool:
        return {'status': 'no_candidates', 'actions': actions, 'findings': [], 'delivery': 'blocked',
                'unsearched_directions': len(directions) - len(actions),
                'model_usage': path_result.get('token_usage', {}),
                'phase_usage': [path_result.get('token_usage', {})],
                'model_seconds': path_result.get('model_seconds'),
                'limitations': ['В выбранных направлениях не найдено новых кандидатов в снимке; остальные направления не исследовались.']}
    ranked = ranker({**profile, 'contest': {'candidates': pool}})
    by_id = {x['offer_id']: x for x in pool}
    ordered = ranked['candidates']
    if len(ordered) != len(pool) or {x['offer_id'] for x in ordered} != set(by_id):
        raise ValueError('invalid model candidate order')
    findings = []
    history_refs_by_item = {str(h['item_id']): f'history-{i + 1}'
                            for i, h in enumerate(history)}
    weak_basis_count = 0
    for choice in ordered:
        assessment = choice['assessment']
        # Uncertain and weak links are not promoted merely to fill slots.
        if assessment['category'] in ('uncertain', 'questionable_relation'):
            continue
        # A path is attached to one history item. Do not present that path as
        # the explanation for a model hypothesis about a different item.
        candidate = by_id[choice['offer_id']]
        path_history = {history_refs_by_item.get(str(item))
                        for item in candidate['evidence']['history_item_ids']}
        if not path_history.intersection(assessment.get('history_ids', [])):
            continue
        basis = set(assessment.get('basis', []))
        if basis and basis <= {'name_difference', 'apparent_product_role'}:
            weak_basis_count += 1
        # Neither a name-based model label nor a source about one product
        # establishes a customer's task or a relation between two products.
        # Preserve the model's classification for transparency, but never turn
        # it into an affirmative client need or a grounded recommendation.
        findings.append(_as_customer_question(candidate, assessment))
        if len(findings) >= MAX_FINDINGS:
            break
    metrics = [x.get('token_usage', {}) for x in (path_result, ranked)]
    usage = {key: sum(m[key] for m in metrics) if all(type(m.get(key)) is int for m in metrics) else None
             for key in ('input', 'output', 'total')}
    return {'status': 'explored', 'actions': actions, 'findings': findings,
            'examined': len(pool), 'unsearched_directions': len(directions) - len(actions), 'delivery': 'blocked',
            'weak_basis_count': weak_basis_count,
            'model_usage': usage,
            'phase_usage': metrics,
            'model_seconds': round(sum(x.get('model_seconds', 0) for x in (path_result, ranked)), 3),
            'limitations': ['Каталог и наличие — текущий снимок, не live и не исторические.',
                'Сопоставление истории с каталогом не подтверждено; возможны дубли покупок.',
                'Категория и производитель не доказывают совместимость, права или потребность.',
                'Совпадение слов — лишь поисковая подсказка по train-названию, не идентичность и не доказательство связи; похожие покупки могут остаться.',
                'Приоритет общего документа и совпадения слов названий — контекст, а не подтверждение связи товаров; просмотр ограничен первыми 2000 подходящими предложениями на направление.',
                'Различие названий и предполагаемое назначение не обосновывают связь или потребность; число таких показанных гипотез указано отдельно.',
                'Оценки модели — гипотезы; найденные товары требуют проверки.']}
