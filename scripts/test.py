"""Run Workspace tests against an explicitly selected Core checkout or installation."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, help='Compatible Core checkout; otherwise use installed Core')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    paths = [ROOT / 'package', ROOT / 'tests', ROOT / 'plugins/persistent-context-workspace']
    if args.core:
        core_source = args.core.expanduser().resolve() / 'src'
        if not (core_source / 'contextopus/startup.py').is_file():
            parser.error('--core must contain src/contextopus/startup.py')
        paths.insert(0, core_source)
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(map(str, paths)) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    check = subprocess.run([sys.executable, '-c', 'import contextopus.startup; print("Core:", contextopus.startup.__file__)'], env=env)
    if check.returncode:
        parser.error('Install compatible Core in this interpreter or supply --core')
    options = args.pytest_args
    if options[:1] == ['--']:
        options = options[1:]
    return subprocess.call([sys.executable, '-m', 'pytest', *(options or ['-q', 'tests'])], cwd=ROOT, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
