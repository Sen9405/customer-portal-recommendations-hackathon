"""Private offline exploration only. Never import into a serving pipeline.

Run from project root: python -B -m recommendation.private_candidate_diagnostics
The output is a new private data/ file; stdout contains aggregate counts only.
"""
from collections import Counter
from datetime import date, datetime
from html import escape
import json
import os
from pathlib import Path

from recommendation.temporal_selection import fit

CUTOFF = date(2026, 8, 31)


def build_report(rows):
    train = []
    for order, user, week, item in rows:
        if isinstance(week, datetime):
            week = week.date()
        elif isinstance(week, str):
            week = date.fromisoformat(week)
        if not isinstance(week, date) or not all((order, user, item)):
            raise ValueError('Invalid input')
        if week <= CUTOFF:
            train.append((order, user, week, item))
    model = fit(train)
    histories = model['history']
    # Deliberately cover different history lengths; within each bucket select a
    # history with a co-order signal when possible, then deterministic ID order.
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
    for pred in buckets:
        choices = [u for u in users if pred(len(histories[u]))]
        if choices:
            selected.append(min(choices, key=lambda u: (not bool(scores(histories[u])), str(u))))
    # Fill absent buckets without duplicating a history.
    selected += [u for u in users if u not in selected][:5-len(selected)]
    esc = lambda x: escape(str(x), quote=True)
    parts = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; base-uri 'none'; form-action 'none'\">",
             "<meta name='referrer' content='no-referrer'><title>Закрытая диагностика</title></head><body>",
             '<h1>Закрытая диагностика кандидатов — не рекомендации</h1>',
             '<p>Только локально владельцу; не публиковать, не отправлять, не загружать в облако. Выборка не является обезличенной или безопасной для публикации.</p>',
             '<p>Выбраны до пяти разных историй по длине (1, 2, 3, 4–10, более 10 товаров), с предпочтением истории с сигналом совместного заказа; пустые группы дополняются по длине. Выборка преднамеренная, не репрезентативная.</p>',
             '<p>Только продажи train до 31.08.2026 включительно. Идентификаторы товаров и история — закрытые данные. Граф группирует продажи по клиенту, неделе и заказу. Совместная покупка не доказывает совместимость или причинность. Популярность — лишь число строк train. Модель не проверяет качество и не служит выдаче.</p>']
    count = 0
    for num, user in enumerate(selected, 1):
        history = histories[user]
        signal = scores(history)
        ordered = sorted(signal, key=lambda x: (-signal[x], str(x)))
        ordered += [x for x in model['pop'] if x not in history and x not in signal]
        candidates = ordered[:3]
        parts += [f'<section><h2>История {num}</h2>',
                  '<p>Клиент: ' + esc(user) + '; количество уникальных товаров: ' + str(len(history)) + '</p>',
                  '<p>Наблюдённые товары train: ' + ', '.join(esc(x) for x in sorted(history, key=str)) + '</p>',
                  '<p>Полнота истории не подтверждена; отсутствие товара в наблюдённом train не доказывает, что он не куплен.</p>']
        if not candidates:
            parts.append('<p>Кандидатов по имеющимся train-данным нет.</p>')
        for target in candidates:
            count += 1
            if target in signal:
                evidence = 'Совместные заказы по клиенту и неделе: сумма связей с историей = ' + str(signal[target])
                evidence += '; по товарам истории: ' + ', '.join(
                    str(item) + ' = ' + str(model['co'][item][target])
                    for item in sorted(history, key=str) if model['co'].get(item, {}).get(target))
            else:
                evidence = 'Резерв по популярности: строк train с этим товаром = ' + str(sum(1 for row in train if row[3] == target))
            parts += ['<article><h3>Непроверенный кандидат: ' + esc(target) + '</h3>',
                      '<p>Свидетельство: ' + esc(evidence) + '.</p>',
                      '<ul><li>Полнота истории не подтверждена; возможны покупки и алиасы вне наблюдения.</li>',
                      '<li>Сопоставление товара с каталогом и его алиасы не проверялись; идентичность не установлена.</li>',
                      '<li>Наличие не проверялось: ни текущий поставщик, ни историческая доступность не подтверждены.</li>',
                      '<li>Источник связи: только статистика train; нет проверенного источника совместимости или лицензирования.</li></ul>',
                      '<p>Заблокировано для выдачи; это не рекомендация и не решение проверяющего.</p></article>']
        parts.append('</section>')
    parts.append('</body></html>')
    return '\n'.join(parts), {'histories': len(selected), 'candidates': count, 'train_rows': len(train)}


def write_report(path, content, root):
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    if not parent.is_relative_to(Path(root).resolve(strict=True)) or path.name in ('', '.', '..'):
        raise ValueError('Output must be inside data/')
    target = parent / path.name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main():
    import openpyxl
    root = Path('data').resolve(strict=True)
    source = (root / 'dataset.xlsx').resolve(strict=True)
    if not source.is_relative_to(root):
        raise ValueError('Invalid input path')
    wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        report, counts = build_report((tuple(r[:4]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)))
    finally:
        wb.close()
    write_report(root / 'private-candidate-diagnostics-v2.html', report, root)
    print(json.dumps(counts))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Private diagnostic failed; check local inputs and whether output already exists.') from None
