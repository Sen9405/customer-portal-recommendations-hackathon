import json
import sqlite3
from datetime import date

import pytest

from recommendation.contest_recommender import ContestRecommender, _hash, historical_profile


DAY = date(2026, 1, 1)


def params(*pairs):
    return json.dumps([{'name': name, 'value': value} for name, value in pairs])


def build(tmp_path, offers, rows, mapped):
    db, workbook = tmp_path / 'feed.sqlite', tmp_path / 'workbook'
    workbook.write_bytes(b'synthetic')
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE metadata(key TEXT, value TEXT);
            CREATE TABLE offers(offer_id TEXT,name TEXT,vendor TEXT,category_id TEXT,
                                url TEXT,available TEXT,params_json TEXT);
            CREATE TABLE categories(category_id TEXT, parent_id TEXT, name TEXT);
            INSERT INTO categories VALUES ('office', NULL, 'Офисные программы'),
                ('backup', NULL, 'Резервное копирование'), ('backup-child', 'backup', 'Backup product line');
        ''')
        conn.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?,?)', offers)
    mapping = {'provenance': {'workbook_sha256': _hash(workbook), 'index_sha256': _hash(db)},
               'items': {i: {'product_group': 'same-family', 'candidates': [{'offer_id': i}]}
                         for i in mapped}}
    return ContestRecommender(rows, DAY, mapping, db, workbook_path=workbook)


def offer(key, *, category='office', fields='[]', title=None, available='true'):
    return key, title or key, 'vendor', category, '', available, fields


def oids(result):
    return [c['offer_id'] for c in result['candidates']]


def test_demo_filters_explicit_contradictions_in_both_channels(tmp_path):
    license_type = 'Тип лицензии'
    arch = 'Совместимая архитектура/платформа'
    user_type = 'Пользователь'
    offers = [
        offer('owned'),
        offer('renew', fields=params((license_type, 'Продление'))),
        offer('wrong_segment', fields=params((user_type, 'Физлицо'))),
        offer('wrong_arch', fields=params((arch, 'ARMv8'))),
        offer('unavailable', available='false'),
        offer('upgrade', fields=params((license_type, 'Обновление'))),
        offer('seats', fields=params((license_type, 'Расширение лицензии'))),
        offer('new_subscription', fields=params((license_type, 'Подписка'))),
        offer('renew_tail', title='Example (продление лицензии)'),
        offer('renew_duration', title='Example продление на 12 месяцев'),
        offer('renewal_management', title='Renewal Management — add-on'),
        offer('wrong_tail', fields=params((user_type, 'Физлицо'))),
        offer('wrong_arch_tail', fields=params((arch, 'ARMv8'))),
        offer('unavailable_tail', available='false'),
        offer('mixed', fields=params((user_type, 'Физлицо'), (user_type, 'Юрлицо'))),
        offer('unknown_audience', fields=params((user_type, 'Физлицо'), (user_type, 'не определено'))),
        offer('mixed_arch', fields=params((arch, 'ARMv8'), (arch, 'x86-64'))),
        offer('unknown_arch', fields=params((arch, 'ARMv8'), (arch, 'другое или отсутствует'))),
        offer('soft_only', fields=params(('Тип организации', 'Для дома'))),
        offer('unknown_tail', fields='malformed', title='Example без продления'),
    ]
    mapped = [record[0] for record in offers[:8]]
    rows = [('own', 'buyer', DAY, 'owned')]
    rows.extend(('other', 'peer', DAY, key) for key in mapped)
    before = historical_profile(rows, DAY, 'buyer')
    engine = build(tmp_path, offers, rows, mapped)
    got = engine.recommend('buyer', 'B2B', platform='x86-64')
    assert set(oids(got)) == {'upgrade', 'seats', 'new_subscription', 'mixed', 'mixed_arch',
                             'unknown_arch', 'unknown_audience', 'soft_only', 'unknown_tail',
                             'renewal_management'}
    assert set(oids(got)[:3]) == {'upgrade', 'seats', 'new_subscription'}
    assert oids(engine.recommend('buyer', 'B2B', platform='x86-64', limit=3)) == oids(got)[:3]
    assert 'wrong_arch_tail' in oids(engine.recommend('buyer', 'B2B'))
    assert historical_profile(rows, DAY, 'buyer') == before


def test_cold_start_uses_segment_then_goal_without_personal_history(tmp_path):
    offers = [offer('generic'), offer('business', fields=params(('Пользователь', 'Юрлицо'))),
              offer('home', fields=params(('Пользователь', 'Физлицо'))),
              offer('backup', category='backup'), offer('extra_backup', category='backup-child'),
              offer('renew_backup', category='backup', fields=params(('Тип лицензии', 'Продление'))),
              offer('unrelated_feed')]
    mapped = ['generic', 'business', 'home', 'backup']
    rows = [(str(i), f'peer-{i}', DAY, 'generic') for i in range(5)]
    rows += [('1', 'peer-a', DAY, 'business'), ('2', 'peer-b', DAY, 'home'), ('3', 'peer-c', DAY, 'backup')]
    engine = build(tmp_path, offers, rows, mapped)

    general = engine.recommend('visitor', 'B2B')
    assert general['selection_mode'] == 'cold_start_popularity'
    assert oids(general) == ['business', 'generic', 'backup']
    assert oids(engine.recommend('another-visitor', 'B2B')) == oids(general)
    assert oids(engine.recommend('visitor', 'B2C')) == ['home', 'generic', 'backup']
    assert all(c['source'] == 'profile' for c in general['candidates'])

    focused = engine.recommend('visitor', 'B2B', goal='backup')
    assert focused['selection_mode'] == 'cold_start_goal'
    assert focused['goal'] == 'backup'
    assert oids(focused) == ['backup', 'extra_backup', 'business', 'generic']
    assert [c['evidence']['goal_match'] for c in focused['candidates']] == [True, True, False, False]
    assert focused['candidates'][1]['item_id'] is None
    assert engine.recommend('visitor', 'B2B', goal='backup', limit=1)['candidates'] == focused['candidates'][:1]
    unmatched = engine.recommend('visitor', 'B2B', goal='security')
    assert unmatched['selection_mode'] == 'cold_start_popularity'
    assert unmatched['candidates'] == general['candidates']
    assert engine.recommend('peer-a', goal='backup')['candidates'] == engine.recommend('peer-a')['candidates']
    with pytest.raises(ValueError, match='goal'):
        engine.recommend('visitor', goal='free text')
    with pytest.raises(ValueError, match='platform'):
        engine.recommend('visitor', platform='free text')
