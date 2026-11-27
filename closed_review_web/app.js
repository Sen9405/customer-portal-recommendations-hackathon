'use strict';
const byId = id => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const list = byId('recommendation-list'), products = byId('product-list'), graph = byId('graph');
const runButton = byId('run-selection'), agent = byId('agent-result');
const agentGoals = new Set(['mechanical', 'standalone']);
const cold = {kind:'cold', label:'Новый клиент · без покупок', description:'Популярные товары или подборка по вашей задаче.'};
const refusal = {kind:'refusal', label:'Новый клиент · отдельный модуль nanoCAD', description:'Можно ли купить модуль «Механика» отдельно? ИИ проверит условия производителя.', goal:'standalone'};
const isNewCustomer = profile => profile.kind === 'cold' || profile.kind === 'refusal';
let current, revision = 0;

function element(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function link(href, text, origin) {
  try {
    const url = new URL(href);
    if (url.protocol !== 'https:' || url.username || url.password || (origin && url.origin !== origin)) return null;
    const a = element('a', '', text);
    a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  } catch (_) { return null; }
}
function productLink(href, text) { return link(href, text, 'https://store.softline.ru'); }
function title(item) { return item.label.replace(/ \[товар продаж: [^\]]+\]$/, ''); }
function count(value, forms) {
  const n = value % 100, last = n % 10;
  return `${value} ${forms[n > 10 && n < 20 ? 2 : last === 1 ? 0 : last >= 2 && last <= 4 ? 1 : 2]}`;
}
function dateLabel(date) {
  const match = /^(\d{4})-(\d\d)-(\d\d)/.exec(date || '');
  return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
}
async function api(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', 'X-Local-Token':token}, body:JSON.stringify(body), cache:'no-store'});
  if (!response.ok) throw Error('Request failed');
  return response.json();
}
function showSection(id, show) {
  byId(id).hidden = !show;
  const nav = document.querySelector(`nav a[href="#${id}"]`);
  if (nav) nav.hidden = !show;
}
function showHistory(items) {
  byId('product-count').textContent = count(items.length, ['товар', 'товара', 'товаров']);
  products.replaceChildren();
  if (!items.length) {
    products.append(element('p', 'empty', 'Покупок пока нет. Можно выбрать задачу, чтобы уточнить подборку.'));
    return;
  }
  for (const [i, item] of items.entries()) {
    const row = element('div', 'product-row');
    const icon = element('span', 'product-icon', ['▦','▤','◇'][i % 3]); icon.setAttribute('aria-hidden', 'true');
    const text = element('div', 'product-text');
    const weeks = item.week_dates.map(dateLabel).filter(Boolean);
    text.append(element('h3', '', title(item)), element('p', '', count(item.order_count, ['заказ', 'заказа', 'заказов']) + (weeks.length ? ` · ${weeks.length === 1 ? 'Неделя покупки' : 'Последняя неделя покупки'}: ${weeks.at(-1)}` : '')));
    row.append(icon, text); products.append(row);
  }
}
function historyGround(candidate, history) {
  const ev = candidate.evidence || {};
  const name = id => history.get(String(id));
  const basket = (ev.coorder || []).find(row => name(row.history_item));
  const later = (ev.sequential || []).find(row => name(row.last_week_item));
  const edge = (source, relation, fact) => ({from:source, to:candidate.name, relation, fact, status:'По данным продаж'});
  if (candidate.source === 'profile' && basket) {
    const from = name(basket.history_item);
    return {reason:`Этот товар стоит рассмотреть вместе с «${from}»: покупатели уже заказывали их в паре.`, edge:edge(from, 'Покупали вместе', `Совместные заказы: ${basket.order_count}.`)};
  }
  if (candidate.source === 'profile' && later) {
    const from = name(later.last_week_item);
    const fact = later.other_client_count >= 3 ? `Так поступили другие клиенты: ${later.other_client_count}.` : `Покупки в таком порядке встречались в данных продаж.`;
    return {reason:`Покупатели, которые тоже брали «${from}», позже выбирали этот товар.`, edge:edge(from, 'Покупали позже', fact)};
  }
  if (['distinct_client_popularity', 'popularity_fallback'].includes(ev.method)) {
    return {reason:'Если хотите начать с востребованных вариантов, обратите внимание на этот товар: его часто выбирают покупатели.'};
  }
  if (candidate.source === 'related_feed' && (ev.same_category || ev.same_vendor)) {
    const field = ev.same_category ? 'категория' : 'производитель';
    return {reason:`Если ищете похожий вариант, присмотритесь к этому товару: с одной из ваших покупок совпадает ${field}.`};
  }
  return {reason:'Вариант для обзора; связь с конкретной покупкой не установлена.'};
}
function drawCards(candidates, grounds) {
  byId('recommendation-count').textContent = count(candidates.length, ['предложение', 'предложения', 'предложений']);
  const cards = element('div', 'contest-carousel');
  for (const [i, candidate] of candidates.entries()) {
    const card = element('article', 'recommendation');
    const heading = element('h3');
    heading.append(productLink(candidate.url, candidate.name) || candidate.name);
    const reason = element('div', 'customer-reason');
    reason.append(element('strong', '', 'Почему это предложение'), element('p', '', grounds[i].reason));
    card.append(element('span', 'rec-maker', `Предложение ${i + 1}`), heading, reason);
    cards.append(card);
  }
  if (candidates.length) list.append(cards);
}
function drawGraph(edges) {
  graph.replaceChildren();
  showSection('explanation', edges.length > 0);
  if (!edges.length) return;
  const rows = element('ul', 'product-graph');
  for (const edge of edges) {
    const row = element('li', 'graph-edge');
    const route = element('div', 'graph-route');
    const relation = element('span', 'graph-relation');
    const arrow = element('span', 'graph-arrow', '→'); arrow.setAttribute('aria-hidden', 'true');
    relation.append(element('span', '', edge.relation), arrow);
    route.append(element('strong', 'graph-node', edge.from), relation, element('strong', 'graph-node', edge.to));
    const proofText = element('p', 'graph-proof');
    const badge = element('strong', 'graph-status', edge.status);
    badge.dataset.kind = edge.kind || 'sales';
    proofText.append(badge, ` ${edge.fact}`);
    row.append(route, proofText);
    const source = link(edge.url, 'Документ производителя');
    if (source) {
      const proof = element('details', 'candidate-evidence');
      proof.append(element('summary', '', 'Что сказано в документации'), element('blockquote', '', edge.quote), source);
      row.append(proof);
    }
    rows.append(row);
  }
  graph.append(rows);
}
function showDocumented(findings, edges) {
  const panel = element('section', 'benefit-panel');
  panel.append(element('h3', '', 'Что можно сделать с тем, что у вас уже есть'));
  for (const f of findings) {
    const source = link(f.url, 'Документ производителя');
    if (!source || !f.source_fact || !['confirmed','rejected'].includes(f.source_status)) continue;
    const rejected = f.status === 'rejected';
    edges.push({from:f.from, to:f.to, relation:rejected ? 'Не подходит' : f.relation,
      kind:rejected ? 'rejected' : 'conditional', status:rejected ? 'Отклонено' : 'Применимость нужно проверить',
      fact:rejected ? 'Документация исключает такой вариант.' : 'Производитель подтверждает связь между продуктами. Перед покупкой уточните действующую лицензию и версию.', quote:f.source_fact, url:f.url});
    if (rejected) continue;
    const card = element('article', 'benefit-card');
    const heading = element('h4'); heading.append(productLink(f.catalog_url, f.to) || f.to);
    const reason = element('div', 'customer-reason');
    const benefit = f.relation === 'Возможное дополнение EDR' ? 'Если хотите не только защищать рабочие места, но и разбираться в причинах атак, это дополнение стоит рассмотреть. Сначала проверьте, не входит ли эта возможность в вашу лицензию.' : 'Если вам нужен переход на новую версию, начните с этого варианта. Уточните условия для вашей лицензии.';
    reason.append(element('strong', '', 'Почему это предложение'), element('p', '', benefit));
    const evidence = element('a', '', 'Смотреть связь и источник'); evidence.href = '#explanation';
    card.append(heading, reason, evidence); panel.append(card);
  }
  if (panel.children.length > 1) list.append(panel);
}
function snapshotNote(date, prefix = '') {
  const stamp = dateLabel(date);
  if (stamp) list.append(element('p', 'snapshot-note', `${prefix}Каталог от ${stamp}. Актуальное наличие уточняйте перед покупкой.`));
}
function coldGround(candidate, result, goal) {
  if (goal && result.category_relaxed) return {reason:'Это вариант из другой категории для обзора, а не решение указанной задачи.'};
  if (goal) return {reason:'Товар относится к категории вашей задачи. Конкретные требования стоит сверить в карточке товара.'};
  return {reason:result.kind === 'popular' ? 'Популярный товар по данным продаж. Подборка не персональная.' : 'Вариант из каталога для первого знакомства. Подборка не персональная.'};
}
function agentNote(text, steps = [], cached = false) {
  const panel = element('section', 'agent-result');
  panel.append(element('h3', '', 'Что сделал ИИ'), element('p', '', text));
  if (cached) panel.append(element('p', 'snapshot-note', 'Показан результат предыдущей проверки. Новый запрос к ИИ не отправлялся.'));
  if (steps.length) {
    const sequence = element('ol');
    for (const step of steps) sequence.append(element('li', '', step));
    panel.append(sequence);
  }
  agent.replaceChildren(panel);
}
function appendAgentCard(candidate, reasonText) {
  const panel = element('article', 'benefit-card');
  const heading = element('h3'); heading.append(productLink(candidate.url, candidate.name) || candidate.name);
  const reason = element('div', 'customer-reason');
  reason.append(element('strong', '', 'Почему это предложение'), element('p', '', reasonText));
  panel.append(heading, reason);
  agent.querySelector('.agent-result').append(panel);
}
function showAgentUnavailable(limited = false) {
  const prefix = limited ? 'Достигнут временный лимит ИИ-проверок. Повторите через несколько минут.' : 'ИИ-проверка сейчас недоступна.';
  agentNote(prefix + (list.querySelector('.recommendation') ? ' Показанная подборка не проверена ИИ.' : ' Ответ по этому запросу пока не подтверждён.'));
}
async function investigateProtection(profile, edges, request, findings) {
  agentNote('ИИ проверяет, чем можно дополнить защиту рабочих мест…');
  let r;
  try { r = await api('/api/cabinet-research', {id:profile.id}); }
  catch (_) { if (request === revision) showAgentUnavailable(); return; }
  if (request !== revision) return;
  if (r.status === 'research_rate_limited') { showAgentUnavailable(true); return; }
  const labels = {model_selected_hypothesis:'Модель выбрала направление: обнаружение и расследование атак.',
    tool_read_vendor_evidence:'Сервис сверился с документацией Kaspersky.',
    tool_searched_current_feed:'Сервис проверил предложения в каталоге.',
    model_decided_after_tools:'Модель оценила варианты после проверки источника.'};
  const steps = r.trace.map(s => labels[s]).filter(Boolean);
  const c = r.candidate;
  if (!c || !r.source) {
    agentNote(r.status === 'not_added' ? 'ИИ нашёл вариант, но локальная проверка не добавила его: он уже учтён или не подходит для этой истории.' : 'ИИ не добавил отдельный вариант. Основная подборка не изменилась.', steps, r.cached);
    return;
  }
  const alreadyShown = findings.some(f => f.offer_id === c.offer_id && f.status !== 'rejected');
  agentNote(alreadyShown ? 'Модель выбрала дополнение, уже показанное выше. Не дублируем карточку.' : 'Модель нашла дополнительный вариант. Три основные рекомендации не изменились.', steps, r.cached);
  if (alreadyShown) return;
  appendAgentCard(c, 'Если вам важно быстрее замечать атаки и выяснять, что произошло, рассмотрите этот вариант. Проверьте, входит ли функция в вашу текущую лицензию.');
  edges.push({from:r.from,to:c.name,relation:'Возможное дополнение',kind:'conditional',status:'Применимость нужно проверить',fact:'Документ описывает семейство продуктов. Уточните редакцию и состав лицензии.',quote:r.source.quote,url:r.source.url});
  drawGraph(edges);
}
async function investigateCold(goal, request, candidates) {
  agentNote('ИИ ищет ответ в документации производителя…');
  let r;
  try { r = await api('/api/cold-start-agent', {task:goal, audience:'B2B', platform:null, consent:true}); }
  catch (_) { if (request === revision) showAgentUnavailable(); return; }
  if (request !== revision) return;
  if (r.status === 'research_rate_limited') { showAgentUnavailable(true); return; }
  if (r.status === 'research_unavailable') { showAgentUnavailable(); return; }
  if (r.status === 'availability_unknown') {
    agentNote('Проверка не запущена: актуальность каталога не подтверждена.');
    return;
  }
  const steps = r.trace.map(s => s.step === 'chosen' ? `Модель выбрала источник: ${s.label}.` : s.step === 'read' ? 'Сервис прочитал страницу производителя.' : 'Модель выбрала цитату, а сервис сверил её с документом.');
  const rejected = r.status === 'documented_refusal';
  agentNote(rejected ? 'Модель не предложила покупать модуль отдельно: производитель прямо пишет, что отдельно он не продаётся.' : r.offer ? 'Модель нашла подтверждение в документации. Сервис сверил предложение с каталогом и поставил его первым.' : r.source ? 'Назначение подтверждено документацией, но подходящего предложения в каталоге не найдено. Источник указан в графе.' : 'Не удалось подтвердить подходящее предложение по документации.', steps, r.cached);
  if (rejected) {
    list.replaceChildren(element('p', 'empty', 'Отдельно модуль не продаётся. Если он вам нужен, рассмотрите конфигурацию nanoCAD, в которую он входит.'));
    byId('recommendation-count').textContent = '';
  }
  if (r.offer) {
    const matches = [r.offer, ...candidates.filter(c => c.offer_id !== r.offer.offer_id)].slice(0, 3);
    list.replaceChildren();
    snapshotNote(r.snapshot_date);
    drawCards(matches, matches.map((_, i) => ({reason:i === 0 ? 'Если вам нужно выпускать машиностроительные чертежи и работать с 3D-моделями, начните с этой конфигурации: производитель описывает её именно для этих задач.' : 'Дополнительный вариант для обзора. Соответствие конкретной задаче не проверено.'})));
  }
  if (r.source) drawGraph([{from:rejected ? 'Покупка модуля отдельно' : 'Машиностроительные чертежи и 3D-модели', to:r.offer?.name || (rejected ? 'Модуль «Механика»' : r.configuration), relation:rejected ? 'Не продаётся отдельно' : 'Назначение программы', kind:rejected ? 'rejected' : 'confirmed', status:rejected ? 'Отклонено' : 'Подтверждено производителем', fact:rejected ? 'Нужна конфигурация, в которую входит модуль.' : 'Производитель описывает работу с этой задачей.', quote:r.source.quote,url:r.source.url}]);
}
async function render() {
  const request = ++revision;
  const profile = current, goal = profile.goal || byId('cold-goal').value || null, platform = byId('platform').value || null;
  const noHistory = isNewCustomer(profile);
  runButton.disabled = true;
  agent.replaceChildren();
  byId('recommendation-count').textContent = '';
  byId('product-count').textContent = '';
  products.replaceChildren();
  drawGraph([]);
  list.replaceChildren(element('p', 'empty', 'Готовим подборку…'));
  try {
    if (noHistory) {
      if (goal === 'standalone') {
        showHistory([]);
        list.replaceChildren();
        await investigateCold(goal, request, []);
        return;
      }
      const research = agentGoals.has(goal);
      const result = await api('/api/cold-start', {goal:research ? 'cad' : goal, audience:research ? 'B2B' : null, platform:null});
      if (request !== revision) return;
      showHistory([]);
      list.replaceChildren();
      const candidates = result.candidates.slice(0,3);
      const intro = research ? 'Для обзора — общая подборка САПР. Это не ответ на конкретный запрос. ' : goal && result.category_relaxed ? 'В выбранной категории сейчас нет предложений. Ниже — другие товары для обзора. '
        : !goal ? 'Пока нет истории, показываем общую подборку. ' : '';
      snapshotNote(result.snapshot_date, intro);
      drawCards(candidates, candidates.map(c => research ? {reason:result.category_relaxed ? 'Другой вариант для обзора; не ответ на вашу задачу.' : 'Вариант из раздела САПР. Соответствие конкретному запросу здесь не проверено.'} : coldGround(c, result, goal)));
      if (!candidates.length) list.append(element('p', 'empty', 'Сейчас нет предложений для показа.'));
      if (research) await investigateCold(goal, request, candidates);
      else agentNote('В этом сценарии достаточно данных каталога: подборка не требовала проверки документации с помощью модели.');
      return;
    }
    const data = await api('/api/review', platform ? {id:profile.id, platform} : {id:profile.id});
    if (request !== revision) return;
    showHistory(data.item_history);
    list.replaceChildren();
    const contest = data.contest;
    if (!contest) {
      list.append(element('p', 'empty', 'Каталог сейчас недоступен. История покупок сохранена ниже.'));
      return;
    }
    const candidates = contest.candidates.slice(0,3);
    snapshotNote(contest.snapshot_date);
    const history = new Map(data.item_history.map(item => [String(item.item_id), title(item)]));
    const grounds = candidates.map(candidate => historyGround(candidate, history));
    drawCards(candidates, grounds);
    if (!candidates.length) list.append(element('p', 'empty', platform ? 'В каталоге нет предложений с указанной платформой. Можно выбрать другую или снять фильтр.' : 'Подходящих предложений пока нет.'));
    const edges = grounds.flatMap(ground => ground.edge ? [ground.edge] : []);
    if (!platform) showDocumented(data.history_research || [], edges);
    drawGraph(edges);
    if (profile.kind === 'document' && !platform) await investigateProtection(profile, edges, request, data.history_research || []);
    else agentNote(platform ? 'Платформу проверили по указанным характеристикам каталога. Для этой проверки модель не нужна.' : 'Подборка основана на покупках и каталоге. Для этого профиля нет отдельной проверки документации с помощью модели.');
  } catch (_) {
    if (request !== revision) return;
    list.replaceChildren(element('p', 'empty', 'Не удалось загрузить подборку. Попробуйте обновить страницу.'));
  } finally {
    if (request === revision) runButton.disabled = false;
  }
}
function resetSelection() {
  revision++;
  runButton.disabled = false;
  agent.replaceChildren();
  byId('recommendation-count').textContent = '';
  byId('product-count').textContent = '';
  products.replaceChildren(element('p', 'empty', 'История появится вместе с подборкой.'));
  drawGraph([]);
  list.replaceChildren(element('p', 'empty', 'Нажмите «Подобрать рекомендации», чтобы обновить подборку.'));
}
function selectProfile(profile) {
  current = profile;
  byId('profile-title').textContent = `Демо-профиль: ${profile.label}`;
  byId('cold-goal').value = '';
  byId('platform').value = '';
  byId('platform-filter').open = false;
  byId('cold-filters').hidden = profile.kind !== 'cold';
  byId('platform-filter').hidden = isNewCustomer(profile);
  resetSelection();
}
async function loadProfiles() {
  const choices = byId('history-choices');
  try {
    const result = await api('/api/options', {page:0, demo:true});
    showSection('historical-results', result.data_version === 'v5');
    const profiles = [...result.options, cold, refusal];
    for (const [i, profile] of profiles.entries()) {
      const label = element('label'), input = element('input'), text = element('span');
      input.type = 'radio'; input.name = 'profile'; input.value = profile.kind; input.checked = i === 0;
      input.addEventListener('change', () => selectProfile(profile));
      text.append(element('b', '', profile.label), element('small', '', profile.description));
      label.append(input, text); choices.append(label);
    }
    selectProfile(profiles[0]);
  } catch (_) {
    list.replaceChildren(element('p', 'empty', 'Не удалось загрузить демо-профили. Попробуйте обновить страницу.'));
  }
}
byId('cold-goal').addEventListener('change', resetSelection);
byId('platform').addEventListener('change', resetSelection);
runButton.addEventListener('click', render);
loadProfiles();
