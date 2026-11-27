"""Synthetic-only local demo. Run: python3 -B demo_server.py [port]."""
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from recommendation.experimental_graph import fit
from recommendation.hybrid import Hybrid
from recommendation.provenance_links import demo_inputs

WEB = Path(__file__).resolve().parent / 'web'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
SCENARIOS = {
    'b2b': (['item-a'], True, 'Условная компания; отрасль и сегмент не используются.'),
    'b2c': (['item-c'], True, 'Условный частный клиент; сегмент не используется.'),
    'cold': ([], True, 'Синтетическая полная пустая история: резервная популярность.'),
    'partial': (['item-a'], True, 'Искусственное ограничение наличия: из новых предложений доступен только продукт В.'),
    'incomplete': (['item-a'], False, 'Полнота истории не подтверждена: выдача блокируется.'),
    'unresolved': (['unknown'], True, 'Идентичность товара не подтверждена: выдача блокируется.'),
}
NAMES = {'offer-a': 'Синтетический продукт А', 'offer-b': 'Синтетический продукт Б', 'offer-c': 'Синтетический продукт В', 'offer-d': 'Синтетический продукт Г', 'unknown': 'Синтетический товар без сопоставления'}
REASONS = {
    'transaction_graph': ('Граф совместных заказов', 'В синтетических заказах товары встречались вместе. Это ассоциация, не совместимость и не причинность.'),
    'reviewed_family_relation': ('Условная связь семейств', 'Искусственная проверенная связь в фикстуре; она не подтверждает совместимость реальных товаров.'),
    'catalog_similarity': ('Похожесть по категории', 'Общая синтетическая категория означает только похожесть, не совместимость.'),
    'popularity_fallback': ('Резервная популярность', 'Частота в синтетических строках обучения; не персональная потребность и не совместимость.'),
}
BLOCKS = {'history_not_attested_complete': 'История не подтверждена как полная.', 'history_identity_unresolved': 'Не подтверждена идентичность товара в истории.', 'live_availability_unavailable': 'Проверка доступности недоступна.', 'history_offer_missing_or_duplicate': 'Товар истории отсутствует или неоднозначен в каталоге.'}


def fixture_evidence(rec):
    source = rec['source']
    if source == 'transaction_graph':
        return 'Фикстура order-1: продукты А и В в одном вымышленном заказе.'
    if source == 'reviewed_family_relation':
        return 'Фикстура: условная направленная связь семейства А с семейством Б; проверка тоже искусственная.'
    if source == 'catalog_similarity':
        return 'Фикстура каталога: всем четырём продуктам присвоена одна условная категория.'
    return f"Фикстура обучения: строк с этим продуктом — {rec['evidence']['count']}."


def recommend_scenario(scenario):
    history, complete, description = SCENARIOS[scenario]
    snapshot, registry, mappings = demo_inputs()
    conn = sqlite3.connect(':memory:')
    try:
        conn.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);
        CREATE TABLE offers(row_id INTEGER PRIMARY KEY,offer_id TEXT,name TEXT,vendor TEXT,vendor_code TEXT,category_id TEXT,url TEXT,available TEXT,params_json TEXT);
        CREATE INDEX offers_id ON offers(offer_id); CREATE INDEX offers_category_row ON offers(category_id,row_id);''')
        conn.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?)', [
            (i, 'offer-'+letter, NAMES['offer-'+letter], 'Synthetic', None, 'synthetic-category', None, 'true', '[]')
            for i, letter in enumerate('abcd', 1)])
        model = fit([('order-1','user-1','2026-01-01','item-a'), ('order-1','user-1','2026-01-01','item-c'),
                     ('order-2','user-2','2026-01-01','item-d'), ('order-3','user-3','2026-01-01','item-b')])
        identities = {'item-'+letter: 'offer-'+letter for letter in 'abcd'}
        # A fixed simulation, NOT a real live availability service.
        allowed = {'offer-c'} if scenario == 'partial' else set(NAMES)
        checks = []
        def simulated_availability(oid):
            accepted = oid in allowed
            checks.append({'name': NAMES[oid], 'accepted': accepted})
            return accepted
        result = Hybrid(model, conn, snapshot, registry, mappings, verified_identities=identities,
                        availability_check=simulated_availability).recommend(history, history_complete=complete)
        blocked = BLOCKS.get(result['blocked_reason'], 'Выдача заблокирована.') if result['blocked_reason'] else None
        return {'synthetic_only': True, 'description': description,
                'trace': {'history_complete': complete, 'checks': checks, 'blocked_reason': blocked},
                'products': [NAMES.get(identities.get(item, item), 'Синтетический товар') for item in history],
                'blocked_reason': BLOCKS.get(result['blocked_reason'], 'Выдача заблокирована.') if result['blocked_reason'] else None,
                'recommendations': [{'name': NAMES[r['id']], 'source': REASONS[r['source']][0], 'explanation': REASONS[r['source']][1], 'fixture_evidence': fixture_evidence(r)} for r in result['recommendations']]}
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self, *args):
        pass  # Never log request data.

    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.allowed_host():
            return self.send_body(403, b'Forbidden', 'text/plain')
        asset = ASSETS.get(self.path)
        if not asset:
            return self.send_body(404, b'Not found', 'text/plain')
        path = WEB / asset[0]
        if path.is_symlink() or not path.is_file():
            return self.send_body(404, b'Not found', 'text/plain')
        self.send_body(200, path.read_bytes(), asset[1])

    def do_POST(self):
        if not self.allowed_host() or (self.headers.get('Origin') and self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}'):
            return self.send_body(403, b'Forbidden', 'text/plain')
        if self.path != '/api/recommend':
            return self.send_body(404, b'Not found', 'text/plain')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
            return self.send_body(400, b'Bad request', 'text/plain')
        length = int(lengths[0])
        if length < 2 or length > 256:
            return self.send_body(413, b'Invalid body size', 'text/plain')
        if self.headers.get_content_type() != 'application/json':
            return self.send_body(415, b'Expected JSON', 'text/plain')
        try:
            def unique(pairs):
                out = {}
                for k, v in pairs:
                    if k in out: raise ValueError('duplicate key')
                    out[k] = v
                return out
            body = json.loads(self.rfile.read(length).decode('utf-8'), object_pairs_hook=unique)
            if not isinstance(body, dict) or set(body) != {'scenario'} or not isinstance(body['scenario'], str) or body['scenario'] not in SCENARIOS:
                raise ValueError('invalid schema')
        except (ValueError, UnicodeError, RecursionError):
            return self.send_body(400, b'Bad request', 'text/plain')
        response = json.dumps(recommend_scenario(body['scenario']), ensure_ascii=False).encode('utf-8')
        self.send_body(200, response, 'application/json; charset=utf-8')


def main():
    port = int(sys.argv[1]) if len(sys.argv) == 2 else 18765
    if not 1 <= port <= 65535: raise ValueError('invalid port')
    with HTTPServer(('127.0.0.1', port), Handler) as server:
        print(f'Synthetic demo: http://127.0.0.1:{server.server_port}/', flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass


if __name__ == '__main__': main()
