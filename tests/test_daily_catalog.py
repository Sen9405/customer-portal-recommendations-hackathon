from datetime import timedelta
from unittest import TestCase

from catalog.daily_refresh import display_snapshot, feed_time, freshness, generation


def test_feed_date_is_not_download_time_and_expires_at_boundary():
    stamp = '2026-11-27 15:00'
    t = feed_time(stamp)
    assert t is not None
    assert freshness(stamp, t + timedelta(hours=36, seconds=-1)) == 'fresh'
    assert freshness(stamp, t + timedelta(hours=36)) == 'stale'
    assert freshness(stamp, t - timedelta(minutes=11)) == 'future'
    assert freshness(None, t) == 'undated'
    assert freshness('2026-11-27', t) == 'undated'
    assert display_snapshot(stamp, now=t + timedelta(days=7))
    assert not display_snapshot(stamp, live=True, now=t + timedelta(days=7))
    assert not display_snapshot(None)


def test_generation_never_accepts_a_path_as_pointer(tmp_path):
    folder = tmp_path / 'catalog-live'
    folder.mkdir()
    assert generation(tmp_path) is None
    (folder / 'current').write_text('../arbitrary', encoding='ascii')
    with TestCase().assertRaises(ValueError):
        generation(tmp_path)
    active = folder / 'snapshot-abcd1234'
    active.mkdir()
    (folder / 'current').write_text(active.name, encoding='ascii')
    assert generation(tmp_path) == active
