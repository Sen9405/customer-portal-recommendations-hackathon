from datetime import date
from html.parser import HTMLParser
import os
import pytest

from recommendation.private_candidate_diagnostics import build_report, write_report


def rows():
    return [('o1', 'u1', date(2026, 1, 1), 'A<script>'),
            ('o1', 'u1', date(2026, 1, 1), 'B'),
            ('o2', 'u2', date(2026, 1, 1), 'A<script>'),
            ('o3', 'u3', date(2026, 9, 7), 'FUTURE')]


def test_train_only_escaped_and_reasons():
    report, counts = build_report(rows())
    assert 'FUTURE' not in report
    assert 'A&lt;script&gt;' in report and '<script>' not in report
    assert counts['histories'] == 2 and counts['candidates'] > 0
    for text in ('полнота', 'сопоставлен', 'налич', 'источник', 'не рекомендац'):
        assert text in report.lower()
    class Tags(HTMLParser):
        def __init__(self): super().__init__(); self.tags = []
        def handle_starttag(self, tag, attrs): self.tags.append(tag)
    parser = Tags(); parser.feed(report)
    assert not set(parser.tags) & {'script', 'a', 'form', 'iframe', 'img', 'link'}


def test_exclusive_and_root(tmp_path):
    root = tmp_path / 'data'; root.mkdir()
    dest = root / 'report.html'
    write_report(dest, 'private', root)
    assert dest.read_text() == 'private' and os.stat(dest).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): write_report(dest, 'other', root)
    with pytest.raises(ValueError): write_report(tmp_path / 'other', 'private', root)
    assert dest.read_text() == 'private'


def test_selected_at_most_five():
    samples = [(str(i), str(i), date(2026,1,1), str(i)) for i in range(20)]
    _, counts = build_report(samples)
    assert counts['histories'] == 5
