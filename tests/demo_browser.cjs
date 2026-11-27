// Real Chromium executes the shipped HTML/JS against the ephemeral local server.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const origin = `http://127.0.0.1:${process.argv[2]}`;
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // Fail closed: the test must never contact a remote service.
  await page.route('**/*', async route => {
    if (!route.request().url().startsWith(origin + '/')) {
      errors.push('Unexpected external request: ' + route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  try {
    await page.goto(origin + '/', {waitUntil: 'networkidle'});
    const example = page.getByRole('region', {name: /Публичный пример Kaspersky/});
    assert.equal(await example.count(), 1);
    assert.equal(await example.isVisible(), true);
    const exampleText = await example.innerText();
    for (const text of ['12.10', '4.0', '26.11.2026', 'KL4867RAKFS', 'KL4867LAKFS', 'не подтверждены', 'неизвестна', 'не используется']) {
      assert.ok(exampleText.includes(text), text);
    }
    assert.equal(await example.locator('a[href^="https://"]').count(), 7);
    for (const link of await example.locator('a').all()) {
      assert.equal(await link.getAttribute('target'), '_blank');
      assert.match(await link.getAttribute('rel'), /noopener/);
      assert.match(await link.getAttribute('rel'), /noreferrer/);
    }
    assert.equal(await page.locator('#recommendations #public-example').count(), 0);
    const counts = page.locator('#recommendation-count');
    await page.waitForFunction(() => document.querySelector('#recommendation-count').textContent.includes('РЕШЕНИЙ'));
    const b2b = await page.locator('#product-list').innerText();
    assert.match(b2b, /Синтетический продукт А/);
    assert.ok(await page.locator('.recommendation').count() > 0);
    const trace = page.locator('#decision-trace');
    assert.equal(await trace.isVisible(), true);
    for (const step of ['КОНТЕКСТ', 'КАНДИДАТЫ', 'ОСНОВАНИЕ', 'ПРОВЕРКА', 'РЕЗУЛЬТАТ / ОТКАЗ']) assert.match(await trace.innerText(), new RegExp(step));
    assert.match(await trace.innerText(), /order-1/);
    assert.match(await trace.innerText(), /не реальная проверка/);
    await page.locator('.recommendation summary').first().click();
    assert.match(await page.locator('.recommendation details').first().innerText(), /ОБЪЯСНЕНИЕ/);
    await page.locator('input[value=b2c]').check();
    await page.waitForFunction(() => document.querySelector('#product-list').textContent.includes('Синтетический продукт В'));
    assert.notEqual(await page.locator('#product-list').innerText(), b2b);
    await page.locator('input[value=cold]').check();
    await page.waitForFunction(() => document.querySelector('#product-list').textContent.includes('нет покупок'));
    assert.match(await page.locator('#product-count').innerText(), /00/);
    assert.ok(await page.locator('.recommendation').count() > 0);
    assert.match(await counts.innerText(), /РЕШЕНИЙ/);

    await page.getByRole('radio', {name: /Меньше трёх/}).check();
    await page.waitForFunction(() => document.querySelector('#recommendation-list').textContent.includes('1 из 3'));
    assert.equal(await page.locator('.recommendation').count(), 1);
    assert.match(await trace.innerText(), /отказ по имитации наличия/);
    await page.locator('.recommendation summary').first().click();
    assert.match(await page.locator('.recommendation details').first().innerText(), /order-1/);
    await page.getByRole('radio', {name: /История не подтверждена/}).check();
    await page.waitForFunction(() => document.querySelector('#recommendation-list').textContent.includes('заблокированы'));
    assert.equal(await page.locator('.recommendation').count(), 0);
    assert.match(await counts.innerText(), /00/);
    assert.match(await trace.innerText(), /Имитация наличия не вызывалась/);
    assert.match(await trace.innerText(), /Отказ:/);
    await page.getByRole('radio', {name: /Товар не опознан/}).check();
    await page.waitForFunction(() => document.querySelector('#recommendation-list').textContent.includes('идентичность'));
    assert.equal(await page.locator('.recommendation').count(), 0);

    await page.route('**/api/recommend', route => route.fulfill({status: 500, body: 'error'}));
    await page.locator('input[value=b2c]').check();
    await page.waitForFunction(() => document.querySelector('#profile-description').textContent.includes('недоступен'));
    assert.match(await page.locator('#recommendation-list').innerText(), /Не удалось получить результат/);
    assert.equal(await page.locator('.recommendation').count(), 0);
    await page.unroute('**/api/recommend');
    await page.locator('input[value=b2b]').check();
    await page.waitForFunction(() => document.querySelector('#product-list').textContent.includes('Синтетический продукт А'));

    // A slow older response must not overwrite the newly selected profile.
    const a = await (await page.request.post(origin + '/api/recommend', {data: {scenario: 'b2c'}})).text();
    const b = await (await page.request.post(origin + '/api/recommend', {data: {scenario: 'cold'}})).text();
    let oldFinished;
    const finished = new Promise(resolve => { oldFinished = resolve; });
    let oldStarted;
    const started = new Promise(resolve => { oldStarted = resolve; });
    await page.route('**/api/recommend', async route => {
      const scenario = route.request().postDataJSON().scenario;
      if (scenario === 'b2c') {
        oldStarted();
        await new Promise(resolve => setTimeout(resolve, 400));
        await route.fulfill({status: 200, contentType: 'application/json', body: a});
        oldFinished();
      } else {
        await route.fulfill({status: 200, contentType: 'application/json', body: b});
      }
    });
    await page.locator('input[value=b2c]').check();
    await started;
    await page.locator('input[value=cold]').check();
    await finished;
    await page.waitForTimeout(100);
    assert.match(await page.locator('#product-list').innerText(), /нет покупок/);
    await page.unroute('**/api/recommend');
    assert.equal(await example.innerText(), exampleText);
    assert.deepEqual(errors, []);
    console.log('Chromium rendering, scenario switching, blocked/error and recovery passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
