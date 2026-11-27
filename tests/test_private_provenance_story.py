from datetime import date
import pytest
from recommendation.private_provenance_story import build_report
from recommendation.private_candidate_diagnostics import write_report


def test_story_is_bounded_escaped_and_fail_closed():
    rows = [('o', 'u', date(2026, 1, 1), 'a', None, '<script>x</script>'),
            ('o', 'u', date(2026, 1, 1), 'b', None, 'B'),
            ('p', 'v', date(2026, 1, 1), 'c', None, 'C')]
    features = {'items': {'b': {'candidate': {'offer_id': '1', 'url': 'https://example.test/x', 'available': 'true'}}}, 'feed': {}}
    audit = {'items': {'b': {'matches': {'all_union': [{'offer_id': '1'}]}}}}
    html, counts = build_report(rows, features, audit, {'1': 1})
    assert counts['histories'] <= 5 and counts['candidates'] <= 3 * counts['histories']
    assert 'https://example.test/x' in html and '<a ' not in html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'не подтверждена' in html and 'не live' in html
    assert 'совместим' in html and 'не производство' in html


def test_ambiguous_and_colliding_mappings_are_never_shown():
    rows = [('o', 'u', date(2026, 1, 1), 'a', None, 'A'), ('p', 'v', date(2026, 1, 1), 'b', None, 'B')]
    feature = {'candidate': {'offer_id': '1', 'url': 'https://example.test/x'}}
    features = {'items': {'a': feature, 'b': feature}, 'feed': {}}
    audit = {'items': {x: {'matches': {'all_union': [{'offer_id': '1'}]}} for x in 'ab'}}
    html, counts = build_report(rows, features, audit, {'1': 1})
    assert 'https://example.test/x' not in html
    assert counts['mapped'] == 0


def test_write_exclusive(tmp_path):
    target = tmp_path / 'report.html'
    write_report(target, 'one', tmp_path)
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_report(target, 'two', tmp_path)
    assert target.read_text() == 'one'
