"""Build Workspace module release assets without installing or publishing them."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    output = Path(output).expanduser().resolve()
    manifest = json.loads((ROOT / 'module.json').read_text(encoding='utf-8'))
    version = manifest['version']
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    base = json.loads((ROOT / 'package/workspace/resources/base_context/manifest.json').read_text(encoding='utf-8'))
    if project['version'] != version or base['version'] != version:
        raise ValueError('Workspace package, module and documentation versions must agree')
    artifact = output / ('contextopus-workspace-' + version + '.zip')
    latest = output / 'contextopus-workspace.zip'
    checksums = output / 'SHA256SUMS'
    if artifact.exists() or latest.exists() or checksums.exists():
        raise ValueError('Release outputs already exist; choose a fresh destination')
    spec = importlib.util.spec_from_file_location('workspace_module_builder', ROOT / 'scripts/build-module.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.archive(artifact)
    import shutil
    shutil.copy2(artifact, latest)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    with checksums.open('x', encoding='utf-8', newline='\n') as stream:
        for path in (artifact, latest):
            stream.write(digest + '  ' + path.name + '\n')
    return {'version': version, 'module': str(artifact), 'latest_alias': str(latest), 'checksums': str(checksums)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', default=ROOT / 'dist')
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
