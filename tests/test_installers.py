"""Installer wrappers delegate to Core and preserve root/path/exit boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from test_workspace_packaging import script

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installer(tmp_path):
    module = script('build-module.py').build(tmp_path / 'module with spaces')
    commands = tmp_path / 'commands'
    commands.mkdir()
    record = tmp_path / 'arguments.json'
    recorder = commands / 'record.py'
    recorder.write_text('import json, os, pathlib, sys\npathlib.Path(os.environ["WRAPPER_RECORD"]).write_text(json.dumps(sys.argv[1:]))\nraise SystemExit(int(os.environ.get("WRAPPER_EXIT", "0")))\n')
    core = commands / ('contextopus.cmd' if os.name == 'nt' else 'contextopus')
    if os.name == 'nt':
        core.write_text('@"' + sys.executable + '" "' + str(recorder) + '" %*\r\n')
    else:
        core.write_text('#!' + sys.executable + '\n' + recorder.read_text())
        core.chmod(0o755)
    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ.get('PATH', ''),
               WRAPPER_RECORD=str(record))
    return module, record, env


@pytest.mark.skipif(os.name != 'posix', reason='POSIX shell wrapper')
def test_shell_forwards_explicit_root_and_readonly_plan(installer, tmp_path):
    module, record, env = installer
    root = tmp_path / 'data root'
    subprocess.run(['/bin/sh', str(module / 'install.sh'), '--root', str(root), '--plan'], env=env, check=True)
    assert json.loads(record.read_text()) == ['--root', str(root), 'modules', 'install', 'workspace', '--source', str(module), '--plan']
    assert not root.exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX shell wrapper')
def test_shell_uses_bound_root_and_propagates_core_failure(installer):
    module, record, env = installer
    result = subprocess.run(['/bin/sh', str(module / 'install.sh')], env=dict(env, WRAPPER_EXIT='2'))
    assert result.returncode == 2
    assert json.loads(record.read_text()) == ['modules', 'install', 'workspace', '--source', str(module)]


@pytest.mark.skipif(os.name != 'posix', reason='POSIX shell wrapper')
def test_missing_core_is_actionable_and_checkout_is_not_installed(installer):
    module, record, env = installer
    missing = subprocess.run(['/bin/sh', str(module / 'install.sh')], env=dict(env, PATH=''), text=True, capture_output=True)
    assert missing.returncode == 127 and 'https://github.com/pikiby/contextopus-core' in missing.stderr
    assert not record.exists()
    checkout = subprocess.run(['/bin/sh', str(ROOT / 'install.sh')], env=env, text=True, capture_output=True)
    assert checkout.returncode == 2 and 'Build the module first' in checkout.stderr
    assert not record.exists()


@pytest.mark.skipif(shutil.which('pwsh') is None, reason='Requires PowerShell')
def test_powershell_forwards_root_and_preview(installer, tmp_path):
    module, record, env = installer
    # Multiple PATH matches must resolve to the first executable, not an array.
    fallback = tmp_path / 'another Core installation'
    fallback.mkdir()
    other_core = fallback / ('contextopus.cmd' if os.name == 'nt' else 'contextopus')
    if os.name == 'nt':
        other_core.write_text('@exit /b 91\r\n')
    else:
        other_core.write_text('#!' + sys.executable + '\nraise SystemExit(91)\n')
        other_core.chmod(0o755)
    first, *remaining = env['PATH'].split(os.pathsep)
    env = dict(env, PATH=os.pathsep.join([first, str(fallback), *remaining]))
    root = tmp_path / 'data root'
    subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(module / 'install.ps1'),
                    '-Root', str(root), '-Plan'], env=env, check=True)
    assert json.loads(record.read_text()) == ['--root', str(root), 'modules', 'install', 'workspace', '--source', str(module), '--plan']
    assert not root.exists()


def test_release_assets_are_complete_and_checksum_both_download_names(tmp_path):
    import hashlib
    import zipfile
    release = script('build-release.py').build(tmp_path / 'release')
    first = Path(release['module'])
    alias = Path(release['latest_alias'])
    assert first.read_bytes() == alias.read_bytes()
    sums = Path(release['checksums']).read_text().splitlines()
    assert len(sums) == 2
    for entry in sums:
        digest, name = entry.split('  ')
        assert digest == hashlib.sha256((first.parent / name).read_bytes()).hexdigest()
    with zipfile.ZipFile(first) as bundle:
        names = set(bundle.namelist())
        assert {'workspace/module.json', 'workspace/LICENSE', 'workspace/NOTICE',
                'workspace/install.sh', 'workspace/install.ps1'} <= names
        assert not any('/archive/' in name or '/tests/' in name or '/.git/' in name for name in names)
