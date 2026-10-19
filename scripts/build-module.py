"""Build the separate Workspace folder module without installing or starting it."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_files(source):
    source = Path(source).resolve()
    manifest = json.loads((source / 'module.json').read_text(encoding='utf-8'))
    if manifest.get('id') != 'workspace' or not (source / 'package').is_dir():
        raise ValueError('Expected a Workspace module.json and package directory')
    result = [source / 'module.json']
    for name in ('LICENSE', 'NOTICE', 'README.md', 'install.sh', 'install.ps1'):
        path = source / name
        if path.is_file():
            if path.is_symlink():
                raise ValueError('Module source symlinks are not supported')
            result.append(path)
    documentation = source / 'docs'
    if documentation.is_dir():
        for path in sorted(documentation.glob('*.md')):
            if path.is_symlink():
                raise ValueError('Module source symlinks are not supported')
            result.append(path)
        for name in ('contextopus-wide-1200.svg', 'contextopus-logo.png'):
            path = documentation / 'media' / name
            if path.is_file():
                if path.is_symlink():
                    raise ValueError('Module source symlinks are not supported')
                result.append(path)
    for path in sorted((source / 'package').rglob('*')):
        if (any(part in {'__pycache__', '.git', '.pytest_cache', '.ruff_cache', '.venv'}
                or part.endswith(('.egg-info', '.dist-info')) for part in path.parts)
                or path.suffix in {'.pyc', '.pyo'}):
            continue
        if path.is_symlink():
            raise ValueError('Module source symlinks are not supported')
        if path.is_file():
            result.append(path)
    return result


def build(output, source=None):
    source = Path(source or ROOT).resolve()
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new folder')
    inputs = source_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.workspace-module-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'workspace'
        for path in inputs:
            target = stage / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        stage.rename(output)
    return output


def archive(output, source=None):
    source = Path(source or ROOT).resolve()
    output = Path(output).expanduser().resolve()
    inputs = source_files(source)
    if output.exists():
        raise ValueError('Archive already exists; choose a new file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in inputs:
            bundle.write(path, 'workspace/' + path.relative_to(source).as_posix())
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New folder containing module.json and package/')
    parser.add_argument('--archive', type=Path, help='Also create a separate Workspace ZIP archive')
    arguments = parser.parse_args()
    prepared = build(arguments.output)
    print(prepared)
    if arguments.archive:
        print(archive(arguments.archive, source=prepared))
