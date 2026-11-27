import os
import threading


from playwright.sync_api import sync_playwright
import closed_review_server as app
from test_closed_review_server import fixture


def test_design_desktop_mobile_and_evidence(tmp_path):
    data = fixture()
    original = data.review
    def review(handle, platform=None):
        result = original(handle, platform)
        result['item_history'] = [{'item_id': 'synthetic-history', 'label': 'Тестовая история <script data-injected>', 'week_dates': [], 'week_count': 1, 'order_count': 1}]
        result['contest'] = {'snapshot_date': '2026-11-26', 'candidates': [
            {'offer_id': str(i), 'name': f'Тестовый продукт {i}', 'vendor': 'Тестовый производитель',
             'category_id': 'тест', 'source': 'profile', 'audience_hint': 'unknown',
             'evidence': {'method': 'association', 'coorder': [{'history_item': 'synthetic-history', 'order_count': 4}],
                          'sequential': [{'last_week_item': 'synthetic-history', 'transition_count': 2}], 'train_unique_order_count': 7}}
            for i in range(1, 6)]}
        result['history_research'] = [{'from':'Тестовая история', 'to':'Дополнение', 'relation':'Дополняет',
            'source_fact':'Проверяемая цитата', 'url':'https://example.com/doc', 'source_status':'confirmed',
            'status':'needs_check', 'customer_goal':'Получить тестовую функцию', 'catalog_url':'https://store.softline.ru/test/'}]
        return result
    data.review = review
    server = app.LocalServer(0, data)
    server.demo_options = [{'id': next(iter(data.handles)), 'kind':'document', 'label':'Компания', 'description':'Пример'}]
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
            for label, width, height in [('desktop', 1440, 900), ('mobile', 390, 844)]:
                page = browser.new_page(viewport={'width': width, 'height': height}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda _: errors.append('pageerror'))
                page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(origin + '/') else r.abort())
                page.goto(origin, wait_until='domcontentloaded')
                page.locator('#run-selection').click()
                page.wait_for_selector('.contest-carousel .recommendation')
                assert page.locator('.contest-carousel .recommendation').count() == 3
                primary = page.locator('.contest-carousel > .recommendation')
                assert primary.count() == 3
                assert primary.locator('.customer-reason').count() == 3
                offsets = primary.locator('.customer-reason').evaluate_all('es => es.map(e => e.getBoundingClientRect().y)')
                assert len(set(offsets)) == (1 if label == 'desktop' else 3)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert 'Совместные заказы: 4' in page.locator('#explanation').inner_text()
                assert not page.locator('.recommendation .candidate-evidence').count()
                assert 'переход на новую версию' in page.locator('.benefit-card .customer-reason').inner_text()
                detail = page.locator('#explanation .candidate-evidence').first
                detail.locator('summary').focus(); page.keyboard.press('Enter')
                assert detail.locator('blockquote').is_visible()
                assert 'Проверяемая цитата' in detail.inner_text()
                assert page.locator('script[data-injected]').count() == 0
                page.locator('#recommendations').scroll_into_view_if_needed()
                path = tmp_path / f'closed-review-design-{label}.png'
                page.screenshot(path=str(path), full_page=True)
                os.chmod(path, 0o600)
                assert not errors
                page.close()
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join()
