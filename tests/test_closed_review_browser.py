import threading
import http.client
import json

import pytest
from playwright.sync_api import expect, sync_playwright
import closed_review_server as app
from test_closed_review_server import fixture


def exercise(data):
    server=app.LocalServer(0,data)
    server.demo_options = app.demo_profiles(data, {})
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':900}); errors=[]
            page.on('pageerror',lambda _: errors.append('page error'))
            def route(r):
                if not r.request.url.startswith(origin+'/'): errors.append('external'); r.abort()
                else: r.continue_()
            page.route('**/*',route); page.goto(origin+'/',wait_until='domcontentloaded')
            expect(page.get_by_role('heading', name='Личный кабинет клиента')).to_be_visible()
            expect(page.locator('input[name=profile]')).to_have_count(len(server.demo_options) + 2)
            assert page.request.get(origin+'/styles.css').body()==(app.ROOT/'web/styles.css').read_bytes()
            if data.contest:
                assert [p['kind'] for p in server.demo_options] == ['multi', 'single', 'private']
                expect(page.locator('#historical-results')).to_be_visible()
                expect(page.locator('#historical-results tbody tr')).to_have_count(3)
                seen = set()
                page.locator('#profile-title').click()
                run = page.locator('#run-selection')
                expect(page.locator('.recommendation')).to_have_count(0)
                for option in server.demo_options:
                    user = data.handles[option['id']]
                    items = data.pipeline.history[user]
                    assert all(data.contest._unique(i) for i in items)
                    if option['kind'] != 'multi':
                        page.locator(f"input[value={option['kind']}]").check()
                    with page.expect_response('**/api/review'):
                        run.click()
                    expect(page.locator('.recommendation .customer-reason')).to_have_count(3)
                    cards = frozenset(page.locator('.recommendation h3').all_text_contents())
                    assert len(cards) == 3 and cards not in seen
                    seen.add(cards)
                    expect(page.locator('.graph-route').first).to_be_visible()
                page.locator('input[value=cold]').check()
                with page.expect_response('**/api/cold-start'):
                    run.click()
                expect(page.locator('#product-list')).to_contain_text('Покупок пока нет')
                expect(page.locator('#explanation')).to_be_hidden()
                before = page.locator('.recommendation h3').all_text_contents()
                page.locator('#cold-goal').select_option('backup')
                with page.expect_response('**/api/cold-start'):
                    run.click()
                expect(page.locator('.recommendation .customer-reason').first).to_contain_text('категории вашей задачи')
                assert page.locator('.recommendation h3').all_text_contents() != before
            for word in ('Исторический топ', 'ДИАГНОСТИКА НЕ ПРОВЕРЕНА', 'Кандидатов нет', 'co-purchase', 'выдача заблокирована'):
                assert word not in page.locator('main').text_content()
            assert page.locator('script[data-injected]').count()==0
            assert not errors
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join()

def test_fixture_browser():
    data=fixture()
    exercise(data)


def test_cabinet_budget_recovers_without_restart(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(app, 'monotonic', lambda: now)
    with app.LocalServer(0, fixture()) as server:
        assert all(server.reserve_cabinet_run() for _ in range(8))
        assert not server.reserve_cabinet_run()
        now += 600
        assert server.reserve_cabinet_run()
        server.cabinet_pause_until = now + 60
        assert not server.reserve_cabinet_run()
        now += 60
        assert server.reserve_cabinet_run()


def test_public_research_cache_is_scoped_and_expires(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(app, 'monotonic', lambda: now)
    with app.LocalServer(0, fixture()) as server:
        key = ('public-cad', 'mechanical', 'B2B', None)
        result = {'status': 'candidate', 'source': {'quote': 'public quote'}}
        server.save_public_result(key, result)
        result['fallback'] = ['local-only']
        assert server.get_public_result(key) == {'status': 'candidate', 'source': {'quote': 'public quote'}, 'cached': True}
        assert server.get_public_result(('public-cad', 'standalone', 'B2B', None)) is None
        assert server.get_public_result(('public-cad', 'mechanical', 'B2B', 'armv8')) is None
        now += 3600
        assert server.get_public_result(key) is None

def test_real_data_browser_smoke():
    try:
        data=app.load_data(); assert len(data.options)>5; exercise(data)
    except Exception:
        pytest.fail('Real-data browser smoke failed; details suppressed for privacy',pytrace=False)

def test_real_data_http_methods_match_official_train():
    server = None
    thread = None
    try:
        from evaluation.protocol_audit import fit_official, recommend_official
        import openpyxl
        from recommendation.profile_pipeline import _day
        data=app.load_data()
        wb=openpyxl.load_workbook(app.ROOT/'data/dataset_v5.xlsx',read_only=True,data_only=True)
        try:
            train=[r[:4] for r in wb['Продажи'].iter_rows(min_row=2,values_only=True) if _day(r[2]) <= app.CUTOFF]
        finally: wb.close()
        official=fit_official(train)
        handle=next(h for h,u in data.handles.items() if official['history'].get(u) and recommend_official(official,official['history'][u],'copurchase'))
        user=data.handles[handle]
        server=app.LocalServer(0,data); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        host=f'127.0.0.1:{server.server_port}'
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        c.request('POST','/api/review',json.dumps({'id':handle}),{'Origin':'http://'+host,'Content-Type':'application/json','X-Local-Token':server.token})
        response=c.getresponse(); assert response.status==200
        result=json.loads(response.read()); c.close()
        for method in ('popularity','copurchase'):
            assert [c['item_id'] for c in result['methods'][method]] == recommend_official(official,official['history'][user],method)
        assert [c['item_id'] for c in result['methods']['profile']] == [c['item_id'] for c in data.pipeline.profile(user)['candidates']]
    except Exception:
        pytest.fail('Real-data HTTP comparison failed; details suppressed for privacy',pytrace=False)
    finally:
        if server is not None:
            server.shutdown(); server.server_close()
            if thread is not None: thread.join()
