"""Guard a public Git index, or export a minimal reviewed source tree."""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXACT = frozenset('README.md SECURITY.md CHANGELOG.md .gitignore closed_review_server.py demo_server.py requirements.in requirements.txt requirements-dev.in requirements-dev.txt tools/check_release.py .github/workflows/ci.yml docs/V5_CONTEST_RESULTS.md docs/V5_COLD_START_EVAL.md docs/V4_BASELINE_COMPARISON.md sources/registry.py sources/manufacturer_relations.json sources/public_renewal_candidate.json'.split())
TYPES = {'recommendation': {'.py'}, 'catalog': {'.py'}, 'evaluation': {'.py'},
         'tests': {'.py', '.cjs'}, 'web': {'.js', '.html', '.css'},
         'closed_review_web': {'.js', '.html', '.css'}}
SECRET = re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,})\b|\b[0-9]{8,12}:[A-Za-z0-9_-]{32,}\b|(?:postgres(?:ql)?|mysql|https?)://[^\s/:@]+:[^\s/@]+@')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def allowed(name):
    p = PurePosixPath(name)
    return name in EXACT or (len(p.parts) == 2 and not p.name.startswith('.')
                            and p.suffix in TYPES.get(p.parts[0], ()))


def validate(name, content):
    if len(content) > 1024 * 1024 or b'\0' in content:
        raise SystemExit(f'Binary or oversized file: {name}')
    if SECRET.search(content):
        raise SystemExit(f'Potential secret (value suppressed): {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path, help='Copy a vetted subset to a new directory outside this repository.')
    args = parser.parse_args()
    records = []
    dest = args.export.resolve() if args.export is not None else None
    if dest is not None:
        if dest.is_relative_to(ROOT):
            parser.error('Export destination must be outside source repository')
        for raw in git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0'):
            name = raw.decode()
            if not allowed(name):
                continue
            path = ROOT / name
            if path.is_symlink() or not path.is_file() or path.resolve() != path:
                raise SystemExit(f'Not a regular source file: {name}')
            records.append((name, path.read_bytes()))
    else:
        for raw in git('ls-files', '--stage', '-z').split(b'\0'):
            if not raw:
                continue
            meta, path = raw.split(b'\t', 1)
            mode, oid, stage = meta.decode().split()
            name = path.decode()
            if stage != '0' or mode not in ('100644', '100755') or not allowed(name):
                raise SystemExit(f'Not allowed in public index: {name}')
            records.append((name, git('cat-file', 'blob', oid)))
    if not records:
        raise SystemExit('No release files to check')
    for name, content in records:
        validate(name, content)
    if dest is not None:
        dest.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, content in records:
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    print(f'{len(records)} files passed release path and secret-pattern checks')


if __name__ == '__main__':
    main()
