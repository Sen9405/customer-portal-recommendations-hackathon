"""Closed, offline diagnostic; never import into serving. Run from project root:

python3 -B -m recommendation.private_provenance_story

Reads local files only; creates one new private HTML in data/. Stdout: aggregates.
This is not a recommender, identity approval, live availability check or backtest.
"""
from collections import Counter
from datetime import date, datetime
from html import escape
import json
from pathlib import Path

from recommendation.private_candidate_diagnostics import write_report
from recommendation.temporal_selection import fit

CUTOFF = date(2026, 8, 31)


def build_report(rows, features, audit, offer_occurrences=None):
    # No index evidence means no mapping is displayed, never assume uniqueness.
    offer_occurrences = offer_occurrences or {}
    train, names = [], {}
    for row in rows:
        order, user, week, item = row[:4]
        if isinstance(week, datetime):
            week = week.date()
        elif isinstance(week, str):
            week = date.fromisoformat(week)
        if not isinstance(week, date) or not all((order, user, item)):
            raise ValueError('invalid sales row')
        if week <= CUTOFF:
            train.append((order, user, week, item))
            if len(row) > 5 and row[5]:
                names.setdefault(item, str(row[5]))
    model = fit(train)
    histories = model['history']
    item_features = features.get('items', {})
    claims = Counter(str(v.get('candidate', {}).get('offer_id')) for v in item_features.values()
                     if v.get('candidate', {}).get('offer_id') is not None)
    def mapping(item):
        feature = item_features.get(str(item), {})
        candidate = feature.get('candidate', {})
        offer = candidate.get('offer_id')
        matches = audit.get('items', {}).get(str(item), {}).get('matches', {}).get('all_union', [])
        if (offer is None or claims[str(offer)] != 1 or offer_occurrences.get(str(offer)) != 1 or len(matches) != 1
                or str(matches[0].get('offer_id')) != str(offer)):
            return None
        return candidate
    def scores(history):
        result = Counter()
        for item in history:
            result.update(model['co'].get(item, {}))
        for item in history:
            result.pop(item, None)
        return result
    users = sorted(histories, key=lambda u: (len(histories[u]), str(u)))
    buckets = [lambda n: n == 1, lambda n: n == 2, lambda n: n == 3,
               lambda n: 4 <= n <= 10, lambda n: n > 10]
    selected = []
    for predicate in buckets:
        choices = [u for u in users if predicate(len(histories[u]))]
        if choices:
            selected.append(min(choices, key=lambda u: (not bool(scores(histories[u])), str(u))))
    selected += [u for u in users if u not in selected][:5-len(selected)]
    esc = lambda x: escape(str(x), quote=True)
    label = lambda x: esc(x) + (' — ' + esc(names[x]) if x in names else '')
    parts = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; base-uri 'none'; form-action 'none'\">",
             "<meta name='referrer' content='no-referrer'><title>Закрытая диагностика</title></head><body>",
             '<h1>Закрытая диагностика — не производство, не рекомендации</h1>',
             '<p>Только локально; не публиковать и не передавать. Данные не обезличены. Не более пяти намеренно выбранных историй разной длины; выборка не репрезентативна.</p>',
             '<p>Факты продаж: только train до 31.08.2026 включительно. Граф — общие заказы одного клиента в одной неделе; значение связи — число таких корзин, сумма по товарам истории может считать одну корзину несколько раз. Это не число источников, не причинность и не совместимость. Резерв — число строк продаж, не число клиентов.</p>',
             '<p>Каталог — локальный снимок текущего фида, не исторический: ' + esc(features.get('feed', {}).get('date_as_supplied', 'дата неизвестна')) + '. Уникальный кандидат сопоставления не подтверждает идентичность, редакцию или права. Наличие в снимке — не live. Это не оценка качества.</p>']
    counts = Counter(histories=len(selected), candidates=0, mapped=0, with_url=0, train_rows=len(train))
    popularity = Counter(row[3] for row in train)
    for number, user in enumerate(selected, 1):
        history = histories[user]
        signal = scores(history)
        ranked = sorted(signal, key=lambda x: (-signal[x], str(x)))
        ranked += [x for x in model['pop'] if x not in history and x not in signal]
        parts.extend([f'<section><h2>История {number}</h2>', '<p>Клиент: ' + esc(user) + '</p>',
                      '<p>Факт: товары наблюдаемого train: ' + ', '.join(label(x) for x in sorted(history, key=str)) + '</p>',
                      '<p>Неизвестно: полнота истории, алиасы и покупки вне train.</p>'])
        for target in ranked[:3]:
            counts['candidates'] += 1
            parts.append('<article><h3>Непроверенный кандидат: ' + label(target) + '</h3>')
            if target in signal:
                detail = '; '.join(label(item) + ': ' + str(model['co'][item][target])
                                   for item in sorted(history, key=str) if model['co'].get(item, {}).get(target))
                parts.append('<p>Источник кандидатуры — статистика совместного заказа train: сумма связей ' + str(signal[target]) + '; по товарам истории: ' + detail + '.</p>')
            else:
                parts.append('<p>Источник кандидатуры — резерв популярности: строк train ' + str(popularity[target]) + '.</p>')
            candidate = mapping(target)
            if candidate:
                counts['mapped'] += 1
                url = candidate.get('url')
                counts['with_url'] += bool(url)
                parts.append('<p>Снимок каталога: единственный кандидат сопоставления, идентичность не подтверждена. Адрес источника (текст, не ссылка): ' + esc(url or 'неизвестен') + '; доступность в снимке: ' + esc(candidate.get('available') or 'неизвестна') + ' (не live).</p>')
            else:
                parts.append('<p>Снимок каталога: однозначное безопасное сопоставление отсутствует или неоднозначно; адрес неизвестен.</p>')
            parts.extend(['<p>Проверенные связи семейства с этим SKU: недоступны; документация семейства не привязана автоматически. Совместимость и лицензирование неизвестны.</p>',
                          '<p>Статус: заблокировано для выдачи. Не подтверждены полнота истории, идентичность/алиасы и live-наличие; автоматических утверждений нет.</p></article>'])
        parts.append('</section>')
    parts.append('</body></html>')
    return '\n'.join(parts), dict(counts)


def main():
    import openpyxl
    root = Path('data').resolve(strict=True)
    def local(name):
        p = (root / name).resolve(strict=True)
        if not p.is_relative_to(root):
            raise ValueError('input outside data')
        return p
    with local('softline-features.json').open(encoding='utf8') as f:
        features = json.load(f)
    with local('softline-audit.json').open(encoding='utf8') as f:
        audit = json.load(f)
    import sqlite3
    db = sqlite3.connect(local('catalog-20261126.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        occurrences = dict(db.execute('SELECT offer_id, COUNT(*) FROM offers GROUP BY offer_id'))
    finally:
        db.close()
    wb = openpyxl.load_workbook(local('dataset.xlsx'), read_only=True, data_only=True)
    try:
        report, counts = build_report(wb['Продажи'].iter_rows(min_row=2, values_only=True), features, audit, occurrences)
    finally:
        wb.close()
    write_report(root / 'private-provenance-story-20261126.html', report, root)
    print(json.dumps(counts))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Private diagnostic failed; check local inputs and whether output already exists.') from None
