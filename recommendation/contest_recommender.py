"""Separate contest prototype, not a historical catalog backtest or production API.

Contract: ContestRecommender(rows, cutoff, mapping, sqlite_path, *, workbook_path)
validates the mapping's input hashes; rows must be TRAIN ONLY through the inclusive
completed-week cutoff (future rows are rejected). Instantiate once; recommend(user,
client_type) returns JSON-serializable data with unique snapshot-available offers.
client_type is 'B2B', 'B2C', or None. Optional goal and platform accept only the
published GOAL_OPTIONS and PLATFORM_OPTIONS. Inputs/outputs are confidential. The default
carousel returns up to 20 offers; only the first three are used by the metric. The mapping
was built from the whole workbook and the current feed, hence demo only. Never use
recommend() to claim leakage-free historical quality. For historical rankings use
historical_profile(), which uses sales rows only and no mapping, feed or customer data.
"""
from collections import Counter, defaultdict
import hashlib
import json
import re
import sqlite3

from recommendation.profile_pipeline import ProfilePipeline, _day
from recommendation.public_research import public_anchor


def _hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _train(rows, cutoff):
    cutoff = _day(cutoff)
    rows = list(rows)
    if any(_day(r[2]) > cutoff for r in rows):
        raise ValueError('future rows supplied; pass train only')
    return rows, cutoff


def historical_profile(rows, cutoff, user):
    """Historical sales-only top 3 item IDs; excludes all observed purchases."""
    rows, cutoff = _train(rows, cutoff)
    return ProfilePipeline(rows, cutoff).profile(user)['candidates']


_USER = 'пользователь'
_ORG_TYPE = 'тип организации'
_ARCH = 'совместимая архитектура/платформа'
_LICENSE = 'тип лицензии'
_BUSINESS = {'юрлицо', 'нко', 'коммерческая', 'академическая', 'государственная', 'некоммерческая'}
_CONSUMER = {'физлицо', 'для дома'}
_USER_TYPES = {'юрлицо', 'нко', 'физлицо'}
PLATFORM_OPTIONS = ('x86-64', 'armv8', 'elbrus')
_ARCH_VALUES = {'x86-64': 'x86-64', 'armv8': 'armv8', 'эльбрус (vliw)': 'elbrus'}
_GOAL_ROOTS = {
    'security': {'антивирусы и безопасность', 'антивирусное по', 'антивирусное по для macos'},
    'backup': {'программное обеспечение для резервного копирования', 'резервное копирование'},
    'office': {'офисные программы'},
    'cad': {'программы сапр и гис'},
}
GOAL_OPTIONS = tuple(_GOAL_ROOTS)
_RENEWAL_LICENSES = {'продление', 'renewal', 'продление технической поддержки для постоянных лиц'}
# Title fallback for explicit qualifiers, not words such as "Renewal Management"
# or a negated phrase such as "без продления".
_RENEWAL_QUALIFIER = re.compile(
    r'(?:^|[(:,;]|\s[–—-]\s)\s*продление\b'
    r'|\bпродление\s*(?:$|[),;]|(?:на|до|от)\s+\d)'
    r'|(?:^|[(:,;]|\s[–—-]\s)\s*renewal\s*(?:$|[),;]|for\b|of\b|\d)', re.IGNORECASE,
)


def _fields(params):
    try:
        entries = json.loads(params)
        if not isinstance(entries, list):
            entries = []
    except (ValueError, TypeError):
        entries = []
    fields = defaultdict(set)
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get('name'), str):
            name = entry['name'].strip().casefold()
            if name in (_USER, _ORG_TYPE, _LICENSE, _ARCH):
                fields[name].add(str(entry.get('value', '')).strip().casefold())
    return fields


def _audience(params, client_type):
    return _audience_from_fields(_fields(params), client_type)


def _audience_from_fields(fields, client_type):
    values = fields[_USER] | fields[_ORG_TYPE]
    if client_type not in ('B2B', 'B2C') or not values & (_BUSINESS | _CONSUMER):
        return 'unknown', 0
    wanted, other = (_BUSINESS, _CONSUMER) if client_type == 'B2B' else (_CONSUMER, _BUSINESS)
    if values & wanted and values & other:
        return 'mixed', 0
    if values & wanted:
        return 'matching_hint', 1
    return 'different_hint', -1


def _offer_policy(offer, client_type, platform):
    """First explicit exclusion reason and, if eligible, the display hint."""
    if offer['availability'] != 'true':
        return 'unavailable', None
    fields = _fields(offer['params'])
    if fields[_LICENSE] & _RENEWAL_LICENSES:
        return 'renewal_catalog_attribute', None
    if _RENEWAL_QUALIFIER.search(offer['name'] or ''):
        return 'renewal_title', None
    hint, weight = _audience_from_fields(fields, client_type)
    if hint == 'different_hint' and fields[_USER] and fields[_USER] <= _USER_TYPES:
        return 'audience_conflict', None
    arch = fields[_ARCH]
    if platform and arch and arch <= _ARCH_VALUES.keys() and platform not in {_ARCH_VALUES[a] for a in arch}:
        return 'platform_conflict', None
    return None, (hint, weight)


def _offer_audience(offer, client_type, platform):
    return _offer_policy(offer, client_type, platform)[1]


def documented_demo_upgrade(contest, research):
    """Join the documented offer for the declared B2B Windows demo only."""
    if research['anchor_id'] != 'cryptopro-csp-server-upgrade' or not any(
        c['status'] == 'confirmed' and c['product_id'] == 'cryptopro-csp-5-server-upgrade'
        and c['relation'] == 'upgrade' and len(c['evidence']) == 2 for c in research['candidates']
    ):
        return None
    offer = contest.by_id.get('197895')
    if not offer or offer['vendor'] != 'КриптоПро' or offer['url'] != 'https://store.softline.ru/kripto-pro/-197895/':
        return None
    fields = _fields(offer['params'])
    if (fields[_LICENSE] != {'обновление'} or fields[_ORG_TYPE] != {'коммерческая'}
            or _offer_audience(offer, 'B2B', None) is None
            or not re.fullmatch(r'КриптоПро CSP версии 5\.0 \(Номер ЕРРП: 4332\), Лицензия на обновление СКЗИ КриптоПро CSP до версии 5\.0 на сервере', offer['name'] or '')):
        return None
    return {'title': offer['name'], 'offer_id': offer['offer_id'], 'url': offer['url'],
            'snapshot_date': contest.metadata.get('feed_date_as_supplied'),
            'conditions': 'Для коммерческой организации на Windows с действующей серверной лицензией прежней версии. Производитель требует оригинал предыдущей лицензии; при ограниченной лицензии — обратиться по месту её приобретения. Сценарий не подтверждает права конкретного клиента или наличие прямо сейчас.'}


def _goal_categories(db):
    if not db.execute("select 1 from sqlite_master where type='table' and name='categories'").fetchone():
        return {}
    categories = list(db.execute('select category_id,parent_id,name from categories'))
    children = defaultdict(set)
    for category, parent, _ in categories:
        children[str(parent)].add(str(category))
    result = {}
    for goal, names in _GOAL_ROOTS.items():
        pending = [str(category) for category, _, name in categories if (name or '').strip().casefold() in names]
        found = set()
        while pending:
            category = pending.pop()
            if category not in found:
                found.add(category)
                pending.extend(children[category] - found)
        result[goal] = found
    return result


def _cold_title_ok(title, client_type):
    title = title or ''
    if re.search(r'\b(?:media[ -]pack|установочный комплект|дистрибутив)\b', title, re.I):
        return False
    # Explicit edition qualifiers supplement incomplete product attributes.
    if client_type == 'B2B' and re.search(
        r'\b(?:для семьи|для дома\b(?!\s+и\s+бизнеса)|microsoft\s+365\s+(?:family|personal|персональн\w*))\b', title, re.I):
        return False
    return not (client_type == 'B2C' and re.search(r'\b(?:для бизнеса|для юрлиц|для организаций)\b', title, re.I))


class ContestRecommender:
    def __init__(self, rows, cutoff, mapping, sqlite_path, *, workbook_path):
        rows, cutoff = _train(rows, cutoff)
        if isinstance(mapping, (str, bytes)) or hasattr(mapping, '__fspath__'):
            with open(mapping, encoding='utf8') as f:
                mapping = json.load(f)
        provenance = mapping.get('provenance', {})
        if provenance.get('workbook_sha256') != _hash(workbook_path) or provenance.get('index_sha256') != _hash(sqlite_path):
            raise ValueError('mapping source hashes mismatch')
        self.pipeline = ProfilePipeline(rows, cutoff)
        self.items = mapping['items']
        self.claims = defaultdict(set)
        for item, entry in self.items.items():
            for c in entry.get('candidates', []):
                if c.get('offer_id') is not None:
                    self.claims[str(c['offer_id'])].add(str(item))
        self.by_id = {}
        self.by_category = defaultdict(list)
        self.by_vendor = defaultdict(list)
        self._cold_catalog_cache = {}
        db = sqlite3.connect('file:' + __import__('urllib.parse', fromlist=['quote']).quote(str(sqlite_path)) + '?mode=ro', uri=True)
        try:
            self.metadata = dict(db.execute('select key,value from metadata'))
            self.goal_categories = _goal_categories(db)
            for row in db.execute('select offer_id,name,vendor,category_id,url,available,params_json from offers'):
                offer, name, vendor, category, url, available, params = row
                if offer is None:
                    continue
                offer = str(offer)
                # Duplicate feed IDs are never usable, including true/false conflicts.
                if offer in self.by_id:
                    self.by_id[offer] = None
                    continue
                self.by_id[offer] = {'offer_id': offer, 'name': name, 'vendor': vendor,
                    'category_id': category, 'url': url, 'availability': available, 'params': params}
            for obj in self.by_id.values():
                if not obj or obj['availability'] != 'true':
                    continue
                if obj['category_id']:
                    self.by_category[str(obj['category_id'])].append(obj)
                if obj['vendor'] and obj['vendor'].strip():
                    self.by_vendor[obj['vendor'].strip().casefold()].append(obj)
        finally:
            db.close()
        self.train_items = {str(item) for item in self.pipeline.popularity}
        self.train_claimed = {str(c['offer_id']) for item in self.train_items
                              for c in self.items.get(item, {}).get('candidates', [])
                              if c.get('offer_id') is not None}

    def _unique(self, item):
        candidates = self.items.get(str(item), {}).get('candidates', [])
        if len(candidates) != 1:
            return None
        c = candidates[0]
        offer = str(c.get('offer_id'))
        if len(self.claims[offer]) != 1 or c.get('duplicate_feed_offer_id'):
            return None
        return self.by_id.get(offer)

    def _cold_candidates(self, goal, audience, limit):
        goal_categories = self.goal_categories.get(goal, set())
        ranked = []
        for position, item in enumerate(self.pipeline.distinct_rank):
            offer = self._unique(item)
            if not offer or (hint_weight := audience(offer)) is None:
                continue
            hint, weight = hint_weight
            match = str(offer['category_id']) in goal_categories
            count = self.pipeline.distinct_clients[item]
            ranked.append((not match, False, -weight, position, offer, 'profile', hint, {
                'method': 'distinct_client_popularity', 'score': count,
                'train_distinct_client_count': count, 'goal_match': match,
            }, item))
        for category in sorted(goal_categories):
            for offer in self.by_category.get(category, ()):
                if offer['offer_id'] in self.train_claimed or (hint_weight := audience(offer)) is None:
                    continue
                hint, weight = hint_weight
                ranked.append((False, True, -weight, offer['offer_id'], offer, 'goal_feed', hint, {
                    'method': 'catalog_goal_category', 'goal_match': True,
                }, None))
        ranked.sort(key=lambda entry: entry[:4])
        return [entry[4:] for entry in ranked[:limit]]

    def cold_popular(self, customers, client_type, industry, goal, platform, *, limit=3):
        return [{'name':o['name'], 'industry_match':industry_hit, 'evidence':{'goal_match':bool(goal)}}
                for _,o,industry_hit in self._cold_popular_rows(customers, client_type, industry, goal, platform, limit=limit)]

    def _cold_popular_rows(self, customers, client_type, industry, goal, platform, *, limit=3, global_scores=False):
        """Strict goal filter; industry popularity backs off within client type."""
        broad, narrow = Counter(), Counter()
        for client, bought in self.pipeline.history.items():
            attrs = customers.get(client, {})
            if client_type is not None and attrs.get('group') != client_type:
                continue
            broad.update(bought)
            if client_type == 'B2B' and industry and attrs.get('industry') == industry:
                narrow.update(bought)
        categories = self.goal_categories.get(goal, set())
        candidates = []
        for item, count in broad.items():
            offer = self._unique(item)
            if not offer or _offer_policy(offer, client_type, platform)[0] is not None:
                continue
            if goal and str(offer['category_id']) not in categories:
                continue
            if platform and platform not in {_ARCH_VALUES[x] for x in _fields(offer['params'])[_ARCH] if x in _ARCH_VALUES}:
                continue
            if not _cold_title_ok(offer['name'], client_type):
                continue
            industry_hit = narrow[item] >= 3
            if global_scores:
                count = self.pipeline.distinct_clients[item]
            candidates.append((not industry_hit, -narrow[item] if industry_hit else -count, str(item), item, offer, industry_hit))
        candidates.sort(key=lambda row: row[:3])
        return [(item,o,industry_hit) for _,_,_,item,o,industry_hit in candidates[:limit]]

    def cold_fallback(self, customers, client_type, industry, goal, platform):
        def popular(scope, arch):
            return self.cold_popular(customers, client_type, industry, scope, arch)

        def catalog(scope, arch):
            key = (client_type, scope, arch)
            if key in self._cold_catalog_cache:
                return self._cold_catalog_cache[key]
            categories = self.goal_categories.get(scope, set())
            pool = (o for c in categories for o in self.by_category.get(c, ())) if scope else self.by_id.values()
            candidates = []
            for offer in pool:
                if not offer or not _cold_title_ok(offer['name'], client_type):
                    continue
                reason, hint = _offer_policy(offer, client_type, arch)
                if reason or not hint or (client_type and hint[0] not in ('matching_hint', 'mixed')):
                    continue
                if arch and arch not in {_ARCH_VALUES[x] for x in _fields(offer['params'])[_ARCH] if x in _ARCH_VALUES}:
                    continue
                candidates.append((-hint[1], offer['name'], offer['offer_id']))
            self._cold_catalog_cache[key] = rows = [{'name':name} for _,name,_ in sorted(candidates)[:3]]
            return rows

        # Only broaden after a wholly empty tier. Never relax availability or
        # audience; unchecked architecture is a separately labelled browse tier.
        for scope, arch in dict.fromkeys(((goal,platform),(None,platform),(goal,None),(None,None))):
            for kind, select in (('popular',popular),('catalog',catalog)):
                if rows := select(scope, arch):
                    return {'candidates':rows, 'kind':kind, 'category_relaxed':bool(goal and not scope),
                            'platform_relaxed':bool(platform and not arch)}
        return {'candidates':[], 'kind':'none', 'category_relaxed':False, 'platform_relaxed':False}

    def recommend(self, user, client_type=None, *, limit=20, goal=None, platform=None, require_platform=False):
        """Use an optional catalog goal only for cold start; never infer platform."""
        result, _ = self._recommend(user, client_type, limit=limit, goal=goal, platform=platform,
                                    require_platform=require_platform, include_pool=False)
        return result

    def recommend_with_catalog_pool(self, user, client_type=None, *, platform=None, require_platform=False):
        """Return the unchanged history carousel and its untruncated feasible pool.

        The private evaluation pool maps offer IDs to train item IDs (or None
        for related-feed offers); it is not the rendered carousel or all offers.
        """
        return self._recommend(user, client_type, limit=20, goal=None, platform=platform,
                               require_platform=require_platform, include_pool=True)

    def _recommend(self, user, client_type, *, limit, goal, platform, require_platform, include_pool):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('limit must be between 1 and 50')
        if client_type not in (None, 'B2B', 'B2C'):
            raise ValueError('client_type must be B2B, B2C or None')
        if goal is not None and goal not in GOAL_OPTIONS:
            raise ValueError('goal must be one of: ' + ', '.join(GOAL_OPTIONS))
        if platform is not None and platform not in PLATFORM_OPTIONS:
            raise ValueError('platform must be one of: ' + ', '.join(PLATFORM_OPTIONS))
        profile = self.pipeline.profile(user)
        seen = {str(x) for x in profile['history_items']}
        if include_pool and not seen:
            raise ValueError('catalog pool comparison requires observed history')
        owned = {offer['offer_id'] for item in seen if (offer := self._unique(item)) is not None}

        def audience(offer):
            if offer['offer_id'] not in owned:
                if require_platform and platform and platform not in {
                        _ARCH_VALUES[x] for x in _fields(offer['params'])[_ARCH] if x in _ARCH_VALUES}:
                    return None
                return _offer_audience(offer, client_type, platform)
            return None

        def eligible(item):
            offer = self._unique(item)
            return offer is not None and audience(offer) is not None

        statistical = []
        blended = self.pipeline.blended_candidates(user, eligible=eligible, profile=profile) if seen else ()
        for c in blended:
            offer = self._unique(c['item_id'])
            if offer is not None:
                hint, _ = _audience(offer['params'], client_type)
                statistical.append((c, offer, hint))
        # Related feed items must share an exact, non-empty category or vendor with
        # an unambiguous history match. Similarity is not compatibility.
        related = {}
        for item in sorted(seen):
            source = self._unique(item)
            if not source:
                continue
            cat = str(source['category_id']) if source['category_id'] else None
            vendor = source['vendor'].strip().casefold() if source['vendor'] else None
            pool = (self.by_category.get(cat, []) if cat else []) + (self.by_vendor.get(vendor, []) if vendor else [])
            for obj in pool:
                oid = obj['offer_id']
                # Never promote unresolved train mappings as new feed offers.
                if oid in self.train_claimed:
                    continue
                same_cat = bool(cat and str(obj['category_id']) == cat)
                same_vendor = bool(vendor and obj['vendor'] and obj['vendor'].strip().casefold() == vendor)
                if not (same_cat or same_vendor):
                    continue
                rec = related.setdefault(oid, {'offer': obj, 'category': False, 'vendor': False, 'history_items': set()})
                rec['category'] |= same_cat
                rec['vendor'] |= same_vendor
                rec['history_items'].add(item)
        ranked = []
        for r in related.values():
            hint_weight = audience(r['offer'])
            if hint_weight is None:
                continue
            hint, weight = hint_weight
            ranked.append((-(int(r['category']) + int(r['vendor'])), -weight, r['offer']['offer_id'], hint, r))
        ranked.sort(key=lambda x: x[:3])
        candidate_pool = {}
        if include_pool:
            # ProfilePipeline can draw from all train items via its fallbacks;
            # do not restrict to the three selected by the blend or the feed tail.
            candidate_pool = {oid: None for _, _, oid, _, _ in ranked}
            for item in self.pipeline.distinct_rank:
                if str(item) not in seen and (offer := self._unique(item)) is not None and audience(offer) is not None:
                    candidate_pool[offer['offer_id']] = item
        result = []
        def add(obj, source, hint, evidence, item_id=None):
            result.append({'offer_id': obj['offer_id'], 'name': obj['name'], 'vendor': obj['vendor'],
                'category_id': obj['category_id'], 'url': obj['url'], 'snapshot_available': True,
                'source': source, 'item_id': item_id, 'identity': 'unverified',
                'audience_hint': hint, 'evidence': evidence})
        # Statistical 2+1 comes first; related-feed items fill gaps and extend it.
        for c, obj, hint in statistical:
            add(obj, 'profile', hint, {'method': c['method'], 'score': c['score'], **c['evidence']}, c['item_id'])
        for _, _, _, hint, r in ranked:
            if len(result) >= 3:
                break
            add(r['offer'], 'related_feed', hint, {'same_category': r['category'], 'same_vendor': r['vendor'],
                'history_item_ids': sorted(r['history_items'])})
        # Preserve the exact first three; expand the carousel only afterwards.
        for _, _, _, hint, r in ranked:
            if len(result) >= limit: break
            if r['offer']['offer_id'] not in {x['offer_id'] for x in result}:
                add(r['offer'], 'related_feed', hint, {'same_category': r['category'], 'same_vendor': r['vendor'],
                    'history_item_ids': sorted(r['history_items'])})
        result = result[:limit]
        selection_mode = 'history_blend'
        if not seen:
            for candidate in self._cold_candidates(goal, audience, limit):
                add(*candidate)
            selection_mode = ('cold_start_goal' if result and result[0]['evidence']['goal_match']
                              else 'cold_start_popularity')
        mapping_status = Counter()
        anchors = {key for candidate in result if (key := public_anchor(candidate['name']))}
        if '4853' in owned or any(c['offer_id'] == '4853' and c['url'] == 'https://store.softline.ru/kaspersky/kl4867lakfs-4853/' for c in result):
            # Public catalog reference only; a tool can investigate the family,
            # not infer the actual client's platform or purchased entitlement.
            reference = self.by_id.get('4853')
            if reference and reference['url'] == 'https://store.softline.ru/kaspersky/kl4867lakfs-4853/':
                anchors.add('kaspersky-endpoint-security-windows')
        for item in seen:
            candidates = self.items.get(item, {}).get('candidates', [])
            if not candidates:
                mapping_status['no_candidate'] += 1
            elif len(candidates) != 1 or (offer := self._unique(item)) is None:
                mapping_status['ambiguous_or_unusable'] += 1
            else:
                mapping_status['unverified_unique'] += 1
                if key := public_anchor(offer['name']):
                    anchors.add(key)
        return {'status': 'preliminary_demo', 'as_of': self.pipeline.as_of.isoformat(),
                'snapshot_date': self.metadata.get('feed_date_as_supplied'), 'client_type': client_type,
                'history_status': profile['status'], 'history_mapping_status': dict(mapping_status), 'candidates': result,
                'selection_mode': selection_mode, 'goal': goal if not seen else None,
                'public_anchors': sorted(anchors),
                'limitations': ['Snapshot availability only; not live or historical.',
                    'Mapping is unverified and built using the whole workbook; ambiguity can hide purchased duplicates.',
                    'Category/vendor similarity is not compatibility, entitlement or proof of need.',
                    'Client type and catalog attributes are undated; only explicit audience/platform conflicts are excluded.',
                    'For no observed history, popularity is nonpersonal; goal category matches are not compatibility.',
                    'Profile uses train only; current mapping/catalog may contain future information; demo is not a historical evaluation.',
                    'The known purchase history may be incomplete; offers absent from mapped train sales are not necessarily novel.']}, candidate_pool
