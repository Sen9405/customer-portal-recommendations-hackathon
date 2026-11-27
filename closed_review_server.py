"""PRIVATE, NOT PRODUCTION: local manager review, never customer delivery.
Run from project root: python3 -B closed_review_server.py [port]
Only 127.0.0.1; stop with Ctrl-C. Inputs are read-only local files.
"""
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import importlib.util
import os
import re
from collections import defaultdict, deque
from pathlib import Path
import secrets
import sqlite3
import sys
from time import monotonic
from typing import cast
from types import ModuleType
from recommendation.contest_recommender import ContestRecommender

from recommendation.profile_pipeline import ProfilePipeline, catalog_evidence
from recommendation.profile_v4_evaluation import official_models, official_rank
from catalog.v4_index_mapping import digest
from catalog.daily_refresh import display_snapshot, generation

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'closed_review_web'
CABINET_ENDPOINTS = frozenset({'/api/options', '/api/review', '/api/cold-start', '/api/cabinet-research', '/api/cold-start-agent'})
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/styles.css': ('styles.css', 'text/css; charset=utf-8'), '/contest.css': ('contest.css', 'text/css; charset=utf-8')}
CUTOFF = date(2026, 8, 31)
PAGE_SIZE = 20

def load_v4_mapping(workbook, index, mapping):
    """Validate provenance and structure; evidence is descriptive, never ranking input."""
    with open(mapping, encoding='utf-8') as f:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('duplicate key')
                result[key] = value
            return result
        source = json.load(f, object_pairs_hook=unique)
    provenance = source['provenance']
    for key, path in (('workbook_sha256', workbook), ('index_sha256', index)):
        expected = provenance[key]
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected) or digest(path) != expected:
            raise ValueError('provenance mismatch')
    items = source['items']
    if not isinstance(items, dict): raise ValueError('invalid items')
    claims = defaultdict(set)
    for item, entry in items.items():
        if not isinstance(item, str) or not item or not isinstance(entry, dict) or type(entry.get('rows')) is not int or entry['rows'] < 1 or not isinstance(entry.get('candidates'), list): raise ValueError('invalid item')
        seen = set()
        for c in entry['candidates']:
            if not isinstance(c, dict) or type(c.get('row_id')) is not int or c['row_id'] <= 0 or c['row_id'] in seen: raise ValueError('invalid row')
            seen.add(c['row_id'])
            offer = c.get('offer_id')
            if offer is not None and (not isinstance(offer, str) or not offer): raise ValueError('invalid offer')
            channels = c.get('match_channels')
            if not isinstance(channels, list) or not channels or any(x not in ('vendorCode', 'url', 'name_exact_N') for x in channels) or len(set(channels)) != len(channels): raise ValueError('invalid channels')
            if c.get('available') not in ('true', 'false', None, '') or type(c.get('duplicate_feed_offer_id')) is not bool: raise ValueError('invalid candidate')
            if offer: claims[offer].add(item)
    metadata = provenance['index_metadata']
    if not isinstance(metadata, dict): raise ValueError('invalid metadata')
    feed_date = metadata.get('feed_date_as_supplied')
    if feed_date is not None and not isinstance(feed_date, str): raise ValueError('invalid date')
    result = {}
    for item, entry in items.items():
        candidates = []
        for c in entry['candidates']:
            candidates.append({'row_id': c['row_id'], 'offer_id': c['offer_id'],
                               'match_channels': c['match_channels'], 'snapshot_available': c['available'],
                               'duplicate_feed_offer_id': c['duplicate_feed_offer_id'],
                               'offer_claim_count': len(claims[c['offer_id']]) if c['offer_id'] else None})
        ambiguous = len(candidates) > 1 or any(not c['offer_id'] or c['duplicate_feed_offer_id'] or c['offer_claim_count'] != 1 for c in candidates)
        result[item] = {'status': 'no_candidate' if not candidates else 'ambiguous' if ambiguous else 'unique',
                        'identity': 'unverified', 'candidates': candidates, 'feed_date': feed_date}
    return result

def enrich_catalog_snapshot(mapping, conn):
    """Attach only fields from the validated snapshot; never attest SKU identity."""
    for entry in mapping.values():
        for candidate in entry['candidates']:
            row = conn.execute('SELECT offer_id,name,vendor,vendor_code,available FROM offers WHERE row_id=?', (candidate['row_id'],)).fetchone()
            if not row or row[0] != candidate['offer_id'] or row[4] != candidate['snapshot_available']:
                raise ValueError('catalog row mismatch')
            for key, value in zip(('snapshot_name', 'snapshot_vendor', 'snapshot_vendor_code'), row[1:4]):
                candidate[key] = str(value)[:500] if value is not None else None
    return mapping


class ReviewData:
    def __init__(self, rows, customers, features, audit, data_version='synthetic', v4_mapping=None):
        rows = list(rows)
        self.data_version = data_version
        self.v4_mapping = v4_mapping
        self.contest: ContestRecommender | None = None
        self.catalog_root: Path | None = None
        self.catalog_generation: Path | None = None
        self.customers = customers
        self.industries = sorted({a['industry'] for a in customers.values()
                                  if a.get('group') == 'B2B' and isinstance(a.get('industry'), str) and a['industry']})
        self.names = {}
        # Names used for display must also come from the cutoff-limited sales.
        from recommendation.profile_pipeline import _day
        train = []
        for r in rows:
            if _day(r[2]) <= CUTOFF:
                train.append((r[0], r[1], _day(r[2]), r[3]))
                if len(r) > 5 and r[5]: self.names.setdefault(r[3], str(r[5]))
        self.train_rows = train
        self.pipeline = ProfilePipeline(train, CUTOFF, customers=customers,
                                        catalog=catalog_evidence(features, audit))
        self.official_pop, self.official_co = official_models(train, self.pipeline.history)
        users = set(customers) | set(self.pipeline.history)
        # Random order and random handles are fresh per server start; no IDs in options.
        ordered = list(users)
        secrets.SystemRandom().shuffle(ordered)
        self.handles = {secrets.token_urlsafe(24): u for u in ordered}
        self.options = [{'id': handle, 'label': 'Клиент ' + str(i)}
                        for i, handle in enumerate(self.handles, 1)]

    def review(self, handle, platform=None):
        if handle not in self.handles: raise ValueError()
        profile = self.pipeline.profile(self.handles[handle])
        profile['data_version'] = self.data_version
        profile['catalog_scope'] = (f'{self.data_version}_snapshot' if self.v4_mapping is not None else f'unavailable_for_{self.data_version}') if self.data_version in ('v4', 'v5') else 'synthetic'
        def v4_catalog(item):
            return (self.v4_mapping or {}).get(str(item), {'status': 'source_unavailable', 'identity': 'unverified', 'candidates': []})
        def label(item): return self.names.get(item, 'Название отсутствует') + ' [товар продаж: ' + str(item) + ']'
        for item in profile['item_history']:
            item['label'] = label(item['item_id'])
            if self.data_version in ('v4', 'v5'): item['catalog'] = v4_catalog(item['item_id'])
        for candidate in profile['candidates']:
            candidate['label'] = label(candidate['item_id'])
            if self.data_version in ('v4', 'v5'): candidate['catalog'] = v4_catalog(candidate['item_id'])
            for reason in candidate['evidence']['coorder']:
                reason['label'] = label(reason['history_item'])
            for reason in candidate['evidence']['sequential']:
                reason['label'] = label(reason['last_week_item'])
        user = self.handles[handle]
        if self.contest is not None:
            group = profile['customer_attributes']['group']['value']
            platform_args = {'platform': platform, 'require_platform': True} if platform else {}
            profile['contest'] = self.contest.recommend(user, group if group in ('B2B', 'B2C') else None, limit=20, **platform_args)
        seen = self.pipeline.history.get(user, set())
        def baseline(method):
            co = self.official_co if method == 'copurchase' else None
            # official_rank accesses hist[user]; cold clients have an empty history.
            history = {user: seen}
            output = []
            for item in official_rank(user, history, self.official_pop, co):
                reasons = [{'label': label(source), 'basket_count': self.official_co[source][item]}
                           for source in sorted(seen, key=str)
                           if self.official_co.get(source, {}).get(item)] if co is not None else []
                output.append({'item_id': item, 'label': label(item),
                               'train_distinct_clients': self.official_pop[item],
                               'coorder': reasons,
                               'catalog': v4_catalog(item) if self.data_version in ('v4', 'v5') else self.pipeline._catalog(item)})
            return output
        profile['methods'] = {'profile': profile['candidates'], 'popularity': baseline('popularity'),
                              'copurchase': baseline('copurchase')}
        from recommendation.closed_agent_workflow import review as agent_review
        profile['agent_review'] = agent_review(profile)
        return profile

def build_data(rows, features, audit, conn=None, customers=None, data_version='synthetic', v4_mapping=None):
    return ReviewData(rows, customers or {}, features, audit, data_version, v4_mapping)


def demo_profiles(data, evidence):
    """Choose distinct, readable histories for the UI, never an evaluation cohort."""
    from recommendation.history_research import from_history

    contest = data.contest
    if contest is None:
        return []
    eligible = []
    for user in sorted(data.pipeline.history, key=str):
        items = data.pipeline.history[user]
        group = data.customers.get(user, {}).get('group')
        if group in ('B2B', 'B2C') and 1 <= len(items) <= 5 and all(contest._unique(x) for x in items):
            eligible.append((user, items, group))
    handles = {u: h for h, u in data.handles.items()}
    options, used_users, used_histories, used_rankings = [], set(), set(), set()
    for kind, label, description in (
        ('document', 'Компания · защита рабочих мест', 'У компании уже есть защита рабочих мест. Можно проверить, чем её дополнить.'),
        ('multi', 'Компания · несколько покупок', 'Рекомендации по нескольким товарам из истории.'),
        ('single', 'Компания · одна покупка', 'Что можно предложить, когда известен только один купленный товар.'),
        ('private', 'Частный покупатель', 'Подборка для физлица с историей покупок.'),
    ):
        for user, items, group in eligible:
            signature = frozenset(items)
            if user in used_users or signature in used_histories or (group == 'B2C') != (kind == 'private'):
                continue
            if (kind == 'single' and len(items) != 1) or (kind == 'multi' and len(items) < 2):
                continue
            if kind == 'document' and not any(
                x['source_status'] == 'confirmed' and x['status'] != 'rejected' and x['url'] and x['source_fact']
                for x in from_history(contest, user, data.names, group, evidence)
            ):
                continue
            candidates = contest.recommend(user, group, limit=3)['candidates']
            ranking = frozenset(c['offer_id'] for c in candidates)
            if len(ranking) != 3 or ranking in used_rankings:
                continue
            if not all(c['source'] == 'related_feed' or c['evidence'].get('coorder') or c['evidence'].get('sequential')
                       or c['evidence'].get('method') in ('distinct_client_popularity', 'popularity_fallback') for c in candidates):
                continue
            if not any(c['evidence'].get('coorder') or c['evidence'].get('sequential') for c in candidates):
                continue
            options.append({'id': handles[user], 'label': label, 'description': description, 'kind': kind})
            used_users.add(user)
            used_histories.add(signature)
            used_rankings.add(ranking)
            break
    return options

def load_data(data_dir=None):
    import openpyxl
    root = Path(data_dir or os.environ.get('CLOSED_REVIEW_DATA_DIR', ROOT / 'data')).resolve(strict=True)
    def local(name):
        p = (root / name).resolve(strict=True)
        if not p.is_relative_to(root): raise ValueError('invalid input')
        return p
    # Use only a v5-specific artifact bound to the exact workbook and feed index.
    workbook = local('dataset_v5.xlsx')
    mapping = None
    index_path = None
    live = generation(root) if os.environ.get('CATALOG_LIVE') == '1' else None
    mapping_path = live / 'mapping.json' if live else root / 'v5-catalog-mapping-20261126.json'
    if mapping_path.exists():
        try:
            index_path = local(str((live / 'catalog.sqlite').relative_to(root)) if live else 'catalog-20261126.sqlite')
            mapping_path = local(str(mapping_path.relative_to(root)))
            mapping = load_v4_mapping(workbook, index_path, mapping_path)
            with sqlite3.connect(index_path.as_uri() + '?mode=ro&immutable=1', uri=True) as conn:
                enrich_catalog_snapshot(mapping, conn)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            mapping = None
    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    try:
        customers = {r[0]: {'group': r[1], 'industry': r[3], 'segment': r[4]}
                     for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True) if r[0] is not None}
        data = build_data(wb['Продажи'].iter_rows(min_row=2, values_only=True), {}, {}, customers=customers, data_version='v5', v4_mapping=mapping)
        data.catalog_root, data.catalog_generation = root, live
        if mapping is not None and index_path is not None:
            data.contest = ContestRecommender(data.train_rows, CUTOFF, mapping_path, index_path, workbook_path=workbook)
        return data
    finally: wb.close()


class LocalServer(HTTPServer):
    def __init__(self, port, data):
        from recommendation.public_research import PublicResearch
        self.data = data
        self.public_research = PublicResearch()
        self.history_evidence = {}
        self.discovery_evidence = {}
        self.offer_agent: ModuleType | None = None
        self.offer_agent_runs = 0
        self.cabinet_attempts = deque()
        self.cabinet_pause_until = 0.0
        self.public_research_cache = {}
        self.cold_agent_enabled = False
        self.cabinet_only = False
        self.demo_options: list[dict[str, str]] | None = None
        self.token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), Handler)
    def handle_error(self, request, client_address): pass

    def reserve_cabinet_run(self):
        now = monotonic()
        while self.cabinet_attempts and self.cabinet_attempts[0] <= now - 600:
            self.cabinet_attempts.popleft()
        if now < self.cabinet_pause_until or len(self.cabinet_attempts) >= 8:
            return False
        self.cabinet_attempts.append(now)
        return True

    def get_public_result(self, key):
        cached = self.public_research_cache.get(key)
        if cached is None:
            return None
        expires_at, result = cached
        if expires_at <= monotonic():
            del self.public_research_cache[key]
            return None
        return {**result, 'cached': True}

    def save_public_result(self, key, result):
        if len(self.public_research_cache) >= 64 and key not in self.public_research_cache:
            del self.public_research_cache[next(iter(self.public_research_cache))]
        self.public_research_cache[key] = (monotonic() + 3600, dict(result))

    def sync_catalog(self):
        if os.environ.get('CATALOG_LIVE') != '1':
            return
        root = getattr(self.data, 'catalog_root', None)
        if root is None:
            return
        try:
            if generation(root) != getattr(self.data, 'catalog_generation', None):
                new = load_data(root)
                if new.contest is not None:
                    # Keep existing browser handles; the workbook itself is unchanged.
                    self.data.contest, self.data.v4_mapping = new.contest, new.v4_mapping
                    self.data.catalog_generation = new.catalog_generation
                    self.public_research_cache.clear()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            pass  # Last validated generation remains subject to freshness.


class Handler(BaseHTTPRequestHandler):
    @property
    def local_server(self) -> LocalServer:
        return cast(LocalServer, self.server)

    protocol_version = 'HTTP/1.0'
    def log_message(self, *args): pass
    def send_body(self, status, body, content_type='text/plain; charset=utf-8'):
        self.send_response(status)
        for k, v in {'Content-Type': content_type, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'", 'Cross-Origin-Resource-Policy': 'same-origin'}.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    def allowed(self):
        host = f'127.0.0.1:{self.local_server.server_port}'
        origins = self.headers.get_all('Origin', [])
        return self.headers.get_all('Host', []) == [host] and (not origins or origins == ['http://' + host]) and self.client_address[0] == '127.0.0.1'
    def cabinet_research(self, request, blocked_availability):
        from recommendation.new_offer_agent import paths_for, public_protection_demo

        local = self.local_server
        if set(request) != {'id'} or type(request['id']) is not str or not any(
            row['id'] == request['id'] and row['kind'] == 'document' for row in (local.demo_options or [])
        ):
            raise ValueError()
        adapter, catalog = local.offer_agent, local.data.contest
        if blocked_availability or adapter is None or not local.discovery_evidence or catalog is None:
            return None
        key = ('public-protection',)
        result = local.get_public_result(key)
        if result is None:
            if not local.reserve_cabinet_run():
                return {'status':'research_rate_limited', 'trace':[]}
            try:
                with adapter.client() as client:
                    result = public_protection_demo(catalog, local.discovery_evidence,
                        lambda instructions, payload: adapter.invoke(client, instructions, payload)[0])
            except Exception as exc:
                if getattr(exc, 'status_code', None) == 429:
                    local.cabinet_pause_until = monotonic() + 60
                    return {'status':'research_rate_limited', 'trace':[]}
                return None
            if result.get('candidate') and result.get('source'):
                local.save_public_result(key, result)  # Before any private history lookup.
        if candidate := result.get('candidate'):
            user = local.data.handles[request['id']]
            profile = local.data.review(request['id'])
            owned, excluded, paths = paths_for(catalog, user, profile['contest']['candidates'][:3], 'B2B')
            if not paths or candidate['offer_id'] in excluded or any(
                (offer.get('name') or '').casefold().startswith('kaspersky edr') for offer in owned.values()
            ):
                result.update(status='not_added', candidate=None)
            else:
                history = local.data.pipeline.history[user]
                result['from'] = next((local.data.names.get(i) for i in history
                    if (offer := catalog._unique(i)) and offer['offer_id'] == '4853' and local.data.names.get(i)),
                    'Kaspersky Endpoint Security для бизнеса')
        return result
    def do_GET(self):
        if not self.allowed(): return self.send_body(403, b'Forbidden')
        asset = ASSETS.get(self.path)
        if not asset: return self.send_body(404, b'Not found')
        try: data = ((ROOT / 'web' / 'styles.css') if self.path == '/styles.css' else (WEB / asset[0])).read_bytes()
        except OSError: return self.send_body(500, b'Local error')
        if self.path == '/':
            data = data.replace(b'__LOCAL_TOKEN__', self.local_server.token.encode())
            if self.local_server.cold_agent_enabled and self.local_server.offer_agent is not None:
                data = data.replace(b'id="cold-agent-panel" hidden', b'id="cold-agent-panel"')
        self.send_body(200, data, asset[1])
    def do_POST(self):
        if not self.allowed() or self.headers.get_all('Origin', []) != [f'http://127.0.0.1:{self.local_server.server_port}']: return self.send_body(403, b'Forbidden')
        if self.local_server.cabinet_only and self.path not in CABINET_ENDPOINTS: return self.send_body(404, b'Not found')
        if self.path not in ('/api/review', '/api/options', '/api/hermes-review', '/api/research', '/api/public-research', '/api/cold-start', '/api/offer-agent', '/api/new-offer-agent', '/api/cold-start-agent', '/api/cabinet-research'): return self.send_body(404, b'Not found')
        tokens = self.headers.get_all('X-Local-Token', [])
        if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.local_server.token): return self.send_body(403, b'Forbidden')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'): return self.send_body(400, b'Bad request')
        size = int(lengths[0])
        if not 2 <= size <= 256: return self.send_body(413, b'Bad request')
        if self.headers.get_content_type() != 'application/json': return self.send_body(415, b'Bad request')
        try:
            def unique(pairs):
                out = {}
                for k, v in pairs:
                    if k in out: raise ValueError()
                    out[k] = v
                return out
            request = json.loads(self.rfile.read(size).decode(), object_pairs_hook=unique)
            if not isinstance(request, dict): raise ValueError()
            self.local_server.sync_catalog()
            catalog = self.local_server.data.contest
            stamp = getattr(catalog, 'metadata', {}).get('feed_date_as_supplied')
            blocked_availability = hasattr(catalog, 'metadata') and not display_snapshot(stamp, live=os.environ.get('CATALOG_LIVE') == '1')
            if blocked_availability and self.path in ('/api/new-offer-agent','/api/offer-agent','/api/research'):
                return self.send_body(503, b'Catalog snapshot is not current')
            if self.path == '/api/options':
                if set(request) not in ({'page'}, {'page', 'demo'}) or type(request['page']) is not int or request['page'] < 0 or ('demo' in request and request['demo'] is not True): raise ValueError()
                if self.local_server.cabinet_only and request.get('demo') is not True: return self.send_body(403, b'Forbidden')
                start = request['page'] * PAGE_SIZE
                options = (self.local_server.demo_options or []) if request.get('demo') else self.local_server.data.options
                result = {'options': options[start:start + PAGE_SIZE], 'has_more': start + PAGE_SIZE < len(options), 'data_version': self.local_server.data.data_version}
                if request['page'] == 0:
                    result['industries'] = self.local_server.data.industries
            elif self.path == '/api/offer-agent':
                from recommendation.agent_offer_gate import run
                from recommendation.contest_recommender import PLATFORM_OPTIONS
                if set(request) != {'id', 'platform', 'consent'} or type(request['id']) is not str or request['platform'] not in PLATFORM_OPTIONS or request['consent'] is not True: raise ValueError()
                local = self.local_server
                adapter = local.offer_agent
                if adapter is None or local.data.contest is None: return self.send_body(503, b'Agent unavailable')
                if local.offer_agent_runs >= 8: return self.send_body(429, b'Local agent demo budget exhausted')
                profile = local.data.review(request['id'])
                local.offer_agent_runs += 1
                def invoke(instructions, payload):
                    with adapter.client() as client:
                        text, _ = adapter.invoke(client, instructions, payload)
                    return text
                try:
                    result = run(local.data.contest, profile['contest']['candidates'], profile['contest']['client_type'], request['platform'], invoke)
                except Exception as exc:
                    if getattr(exc, 'status_code', None) == 429:
                        local.offer_agent_runs = 8
                        return self.send_body(429, b'Model quota exhausted')
                    return self.send_body(503, b'Agent could not verify the selection')
            elif self.path == '/api/cabinet-research':
                result = self.cabinet_research(request, blocked_availability)
                if result is None: return self.send_body(503, b'Research unavailable')
            elif self.path == '/api/new-offer-agent':
                from recommendation.new_offer_agent import propose
                if set(request) != {'id', 'consent'} or type(request['id']) is not str or request['consent'] is not True: raise ValueError()
                local = self.local_server
                adapter = local.offer_agent
                if adapter is None or not local.discovery_evidence or local.data.contest is None:
                    return self.send_body(503, b'Research unavailable')
                if local.offer_agent_runs >= 8: return self.send_body(429, b'Local agent demo budget exhausted')
                profile = local.data.review(request['id'])['contest']
                local.offer_agent_runs += 1
                def invoke_discovery(instructions, payload):
                    with adapter.client() as client:
                        text, _ = adapter.invoke(client, instructions, payload)
                    return text
                try:
                    result = propose(local.data.contest, local.data.handles[request['id']], profile['candidates'],
                                     profile['client_type'], local.discovery_evidence, invoke_discovery)
                except Exception as exc:
                    if getattr(exc, 'status_code', None) == 429:
                        local.offer_agent_runs = 8
                        return self.send_body(429, b'Model quota exhausted')
                    return self.send_body(503, b'Research could not verify a new candidate')
            elif self.path == '/api/public-research':
                from dataclasses import asdict
                from recommendation.public_research import ANCHORS
                if set(request) not in ({'anchor', 'consent'}, {'anchor', 'consent', 'legacy'}) or type(request['anchor']) is not str or request['anchor'] not in ANCHORS or request['consent'] is not True: raise ValueError()
                legacy = request.get('legacy', False)
                if type(legacy) is not bool or (legacy and request['anchor'] != 'kaspersky-endpoint-security-windows'): raise ValueError()
                result = asdict(self.local_server.public_research.run(request['anchor'], allow_network=True))
                result['anchor_name'] = ANCHORS[request['anchor']]
                contest = None if blocked_availability else self.local_server.data.contest
                if contest is not None and request['anchor'] == 'cryptopro-csp-server-upgrade':
                    from recommendation.contest_recommender import documented_demo_upgrade
                    result['catalog_offer'] = documented_demo_upgrade(contest, result)
                if contest is not None and not legacy and request['anchor'] == 'kaspersky-endpoint-security-windows' and any(c['status'] == 'confirmed' for c in result['candidates']):
                    # Public feed offer IDs, not sales IDs. Vendor relation must not
                    # promote this commercially unverified pair to a recommendation.
                    source = contest.by_id.get('4853')
                    target = contest.by_id.get('298271')
                    if (source and target and source['availability'] == target['availability'] == 'true'
                            and source['url'] == 'https://store.softline.ru/kaspersky/kl4867lakfs-4853/'
                            and target['url'] == 'https://store.softline.ru/kaspersky/kl4708rakfs-298271/'):
                        result['catalog_edge'] = {'from': source['name'], 'to': target['name'],
                            'status': 'needs_check', 'relation': 'Позиции текущего фида', 'url': target['url'],
                            'fact': 'Обе позиции были доступны в снимке ' + str(contest.metadata.get('feed_date_as_supplied') or 'неизвестной даты') + '. Не подтверждены версия, право на дополнение и отсутствие дублирования лицензии. У исходной позиции расходится артикул в URL и описании.'}
                if legacy:
                    result['anchor_name'] += ' → EDR Optimum 1.0 (демо-контекст)'
                    for finding in result['candidates']:
                        if finding['status'] == 'confirmed' and any('Interaction with Kaspersky Endpoint Detection and Response Optimum version 1.0 is not supported.' in ref['quote'] for ref in finding['evidence']):
                            finding.update(status='rejected', reason='explicit_version_denial')
            elif self.path == '/api/cold-start-agent':
                from recommendation.cold_start_agent import TASKS, investigate
                from recommendation.contest_recommender import PLATFORM_OPTIONS
                if set(request) - {'industry'} != {'task', 'audience', 'platform', 'consent'} or request['consent'] is not True: raise ValueError()
                task, audience, platform = (request[k] for k in ('task', 'audience', 'platform'))
                for value, options in ((task, TASKS), (audience, ('B2B','B2C')), (platform, PLATFORM_OPTIONS)):
                    if value is not None and (type(value) is not str or value not in options): raise ValueError()
                local = self.local_server
                adapter, contest = local.offer_agent, local.data.contest
                industry = request.get('industry')
                if industry is not None and (audience != 'B2B' or type(industry) is not str or industry not in local.data.industries): raise ValueError()
                if contest is None or not local.cold_agent_enabled: return self.send_body(503, b'Agent unavailable')
                key = ('public-cad', task, audience, platform)
                if blocked_availability:
                    result = {'status': 'availability_unknown', 'trace': []}
                elif task is None:
                    result = {'status': 'needs_goal', 'trace': []}
                elif (cached := local.get_public_result(key)) is not None:
                    result = cached
                elif adapter is None:
                    result = {'status':'research_unavailable', 'trace': []}
                elif not local.reserve_cabinet_run():
                    result = {'status':'research_rate_limited', 'trace': []}
                else:
                    try:
                        with adapter.client() as client:
                            result = investigate(task, audience, platform, contest,
                                                 lambda instruction, payload: adapter.invoke(client,instruction,payload)[0])
                        if result.get('source') and result['status'] in ('candidate', 'documented_refusal'):
                            local.save_public_result(key, result)
                    except Exception as exc:
                        limited = getattr(exc, 'status_code', None) == 429
                        if limited:
                            local.cabinet_pause_until = monotonic() + 60
                        result = {'status':'research_rate_limited' if limited else 'research_unavailable', 'trace': []}
                if not blocked_availability and not result.get('offer'):
                    fallback = contest.cold_fallback(local.data.customers, audience, industry, 'cad' if task else None, platform)
                    result['fallback'] = fallback['candidates']
                    result['fallback_details'] = fallback
                    result['fallback_scope'] = 'cad' if task else 'general'
                    result['snapshot_date'] = stamp
            elif self.path == '/api/cold-start':
                from recommendation.contest_recommender import GOAL_OPTIONS, PLATFORM_OPTIONS
                if set(request) - {'industry'} != {'goal', 'audience', 'platform'}: raise ValueError()
                goal, audience, platform = (request[k] for k in ('goal', 'audience', 'platform'))
                for value, options in ((goal, GOAL_OPTIONS), (audience, ('B2B', 'B2C')), (platform, PLATFORM_OPTIONS)):
                    if value is not None and (type(value) is not str or value not in options): raise ValueError()
                contest = self.local_server.data.contest
                if contest is None: return self.send_body(503, b'Catalog unavailable')
                # Collision-free sentinel; never read any selected customer's history.
                industry = request.get('industry')
                local = self.local_server
                if industry is not None and (audience != 'B2B' or type(industry) is not str or industry not in local.data.industries): raise ValueError()
                if blocked_availability:
                    result = {'selection_mode':'availability_unknown','snapshot_date':stamp,'candidates':[]}
                else:
                    if isinstance(contest, ContestRecommender):
                        result = {'selection_mode':'cold_start_goal' if goal else 'cold_start_popularity',
                                  'snapshot_date':stamp, **contest.cold_fallback(local.data.customers, audience, industry, goal, platform)}
                    else:
                        result = contest.recommend(object(), audience, goal=goal, platform=platform, limit=3)
            elif self.path == '/api/research':
                if set(request) != {'id', 'consent'} or type(request['id']) is not str or request['consent'] is not True: raise ValueError()
                if self.local_server.data.contest is None: return self.send_body(503, b'Catalog unavailable')
                from recommendation.catalog_researcher import explore
                from recommendation.hermes_candidate_review import ReviewError
                try: result = explore(self.local_server.data.review(request['id']), self.local_server.data.contest, allow_name_fallback=True)
                except ReviewError as exc:
                    if str(exc) == 'rate limited': return self.send_body(429, b'Provider rate limited')
                    return self.send_body(503, b'Research unavailable; delivery remains blocked')
            elif self.path == '/api/hermes-review':
                if set(request) != {'id', 'consent'} or type(request['id']) is not str or request['consent'] is not True: raise ValueError()
                from recommendation.hermes_carousel import run, ReviewError
                try: result = run(self.local_server.data.review(request['id']))
                except ReviewError as exc:
                    if str(exc) == 'rate limited': return self.send_body(429, b'Provider rate limited')
                    return self.send_body(503, b'Hermes review unavailable; delivery remains blocked')
            else:
                from recommendation.contest_recommender import PLATFORM_OPTIONS
                if set(request) not in ({'id'}, {'id', 'platform'}) or not isinstance(request['id'], str): raise ValueError()
                if self.local_server.cabinet_only and not any(x['id'] == request['id'] for x in self.local_server.demo_options or []): return self.send_body(403, b'Forbidden')
                platform = request.get('platform')
                if platform is not None and (type(platform) is not str or platform not in PLATFORM_OPTIONS): raise ValueError()
                result = self.local_server.data.review(request['id'], platform) if platform else self.local_server.data.review(request['id'])
                local = self.local_server
                if blocked_availability:
                    result.pop('contest', None)
                    result['availability_unknown'] = True
                    for item in [*result['item_history'], *result['candidates']]:
                        if snapshot := item.get('catalog'):
                            item['catalog'] = {**snapshot, 'candidates':[
                                {**c, 'snapshot_available':None} for c in snapshot.get('candidates', [])]}
                if not blocked_availability and local.offer_agent is not None:
                    result['offer_agent_enabled'] = True
                    if local.discovery_evidence:
                        result['new_offer_agent_enabled'] = True
                if not blocked_availability and local.data.contest is not None and local.history_evidence:
                    from recommendation.history_research import from_history
                    result['history_research'] = from_history(local.data.contest, local.data.handles[request['id']],
                        local.data.names, result['customer_attributes']['group']['value'], local.history_evidence)
            payload = json.dumps(result, ensure_ascii=False).encode()
        except (ValueError, UnicodeError, RecursionError): return self.send_body(400, b'Bad request')
        except Exception: return self.send_body(500, b'Local error')
        self.send_body(200, payload, 'application/json; charset=utf-8')


def main():
    try:
        if len(sys.argv) > 2: raise ValueError()
        port = int(sys.argv[1]) if len(sys.argv) == 2 else 18766
        if not 1 <= port <= 65535: raise ValueError()
        data = load_data()
        with LocalServer(port, data) as server:
            if adapter := os.environ.get('OFFER_AGENT_ADAPTER'):
                spec = importlib.util.spec_from_file_location('trusted_local_offer_adapter', adapter)
                if spec is None or spec.loader is None: raise ValueError()
                server.offer_agent = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server.offer_agent)
            server.cold_agent_enabled = os.environ.get('COLD_START_AGENT') == '1'
            server.cabinet_only = os.environ.get('CABINET_ONLY') == '1'
            if os.environ.get('RESEARCH_PREFETCH_PUBLIC') == '1':
                from recommendation.history_research import prioritize_examples, refresh_public_evidence
                server.history_evidence = refresh_public_evidence(server.public_research)
                prioritize_examples(data, server.history_evidence)
            if server.offer_agent is not None and os.environ.get('OFFER_AGENT_DEMO_FIRST') == '1':
                from recommendation.agent_offer_gate import prioritize_demo
                prioritize_demo(data)
            if server.offer_agent is not None and os.environ.get('NEW_OFFER_AGENT') == '1':
                from recommendation.new_offer_agent import refresh
                from recommendation.history_research import prioritize_examples
                server.discovery_evidence = refresh(server.public_research)
                prioritize_examples(data, {})
            server.demo_options = demo_profiles(data, server.history_evidence)
            print(f'PRIVATE NOT PRODUCTION: http://127.0.0.1:{server.server_port}/', flush=True)
            try: server.serve_forever()
            except KeyboardInterrupt: pass
    except Exception: raise SystemExit('Local review unavailable. Check local inputs and port.') from None


if __name__ == '__main__': main()
