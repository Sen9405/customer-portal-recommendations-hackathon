"""Build and atomically publish a catalog+mapping generation for the closed stand."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MAX_AGE_HOURS = 36
MOSCOW = ZoneInfo('Europe/Moscow')
GENERATION = re.compile(r'snapshot-[a-z0-9_]{8,24}')


def feed_time(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?', value):
            return None
        return datetime.fromisoformat(value).replace(tzinfo=MOSCOW)
    except ValueError:
        return None


def freshness(value, now=None):
    t = feed_time(value)
    if t is None:
        return 'undated'
    age = (now or datetime.now(MOSCOW)) - t
    if age < -timedelta(minutes=10):
        return 'future'
    return 'fresh' if age < timedelta(hours=MAX_AGE_HOURS) else 'stale'


def generation(root):
    try:
        live = root.resolve(strict=True) / 'catalog-live'
        dir_fd = os.open(live, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open('current', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as pointer:
            if not stat.S_ISREG(os.fstat(pointer.fileno()).st_mode):
                raise ValueError('catalog pointer must be a regular file')
            raw = pointer.read(129)
        if len(raw) > 128:
            raise ValueError('catalog pointer too large')
        name = raw.decode('ascii').strip()
    finally:
        os.close(dir_fd)
    if not GENERATION.fullmatch(name):
        raise ValueError('invalid catalog pointer')

    target = (live / name).resolve(strict=True)
    if target.parent != live or not target.is_dir():
        raise ValueError('invalid catalog generation')
    return target


def display_snapshot(value, *, live=False, now=None):
    return feed_time(value) is not None and (not live or freshness(value, now) == 'fresh')


def refresh():
    root = Path('data').resolve(strict=True)
    live = root / 'catalog-live'
    live.mkdir(mode=0o700, exist_ok=True)
    with (live / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = generation(root)
        with tempfile.TemporaryDirectory(prefix='feed-build-', dir=live) as staging:
            stage = Path(staging)
            db, booklet = stage / 'catalog.sqlite', stage / 'mapping.json'
            run = lambda *args: subprocess.run([sys.executable, '-B', '-m', *map(str,args)], check=True, timeout=240, stdout=subprocess.DEVNULL)
            run('catalog.catalog_index', db)
            with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
                meta = dict(conn.execute('SELECT key,value FROM metadata'))
                total, available = conn.execute("SELECT count(*), sum(available='true') FROM offers").fetchone()
            stamp = meta.get('feed_date_as_supplied')
            if freshness(stamp) != 'fresh' or total < 50000 or not available or available < 5000:
                raise ValueError('feed freshness or volume validation failed')
            if old:
                with sqlite3.connect((old / 'catalog.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
                    previous = feed_time(dict(conn.execute('SELECT key,value FROM metadata')).get('feed_date_as_supplied'))
                incoming = feed_time(stamp)
                if previous is not None and (incoming is None or incoming < previous):
                    raise ValueError('feed date moved backwards')
                if previous == incoming:
                    print(json.dumps({'status':'unchanged','feed_date':stamp,'offers':total,'available':available}))
                    return
            run('catalog.v4_index_mapping', root / 'dataset_v5.xlsx', db, booklet)
            name = 'snapshot-' + stage.name.removeprefix('feed-build-')
            final = live / name
            final.mkdir(mode=0o700)
            try:
                db.replace(final / db.name)
                booklet.replace(final / booklet.name)
                tmp = stage / 'pointer'
                tmp.write_text(name+'\n',encoding='ascii')
                tmp.replace(live / 'current')
            except BaseException:
                if generation(root) != final:
                    shutil.rmtree(final)
                raise
            # Leave several days of immutable generations for active readers and rollback.
            cutoff = datetime.now().timestamp() - 7 * 86400
            for p in live.iterdir():
                if p not in (old, final) and GENERATION.fullmatch(p.name) and not p.is_symlink() and p.is_dir() and p.stat().st_mtime < cutoff:
                    shutil.rmtree(p)
            print(json.dumps({'status':'published','generation':name,'feed_date':stamp,'offers':total,'available':available}))


if __name__ == '__main__':
    refresh()
