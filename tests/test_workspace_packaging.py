"""Separate module artifacts stay independent of the Core wheel and working roots."""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workspace_builder_excludes_working_state_and_bytecode_and_refuses_overwrite(tmp_path):
    builder = script('build-module.py')
    source = tmp_path / 'source'
    (source / 'package/__pycache__').mkdir(parents=True)
    (source / 'module.json').write_text(json.dumps({'id': 'workspace', 'version': '1'}))
    (source / 'package/entry.py').write_text('def register(host): pass\n')
    (source / 'package/__pycache__/entry.pyc').write_bytes(b'bytecode')
    (source / 'package/entry.pyo').write_bytes(b'bytecode')
    (source / 'package/workspace.egg-info').mkdir()
    (source / 'package/workspace.egg-info/PKG-INFO').write_text('generated build metadata')
    for state in ('data', 'config', '.venv'):
        (source / state).mkdir()
        (source / state / 'keep').write_text('working state')
    result = builder.build(tmp_path / 'prepared', source)
    assert {path.relative_to(result).as_posix() for path in result.rglob('*') if path.is_file()} == {
        'module.json', 'package/entry.py'}
    with pytest.raises(ValueError, match='already exists'):
        builder.build(result, source)
    archive = builder.archive(tmp_path / 'workspace.zip', result)
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {'workspace/module.json', 'workspace/package/entry.py'}
    with pytest.raises(ValueError, match='already exists'):
        builder.archive(archive, result)


def test_separate_workspace_archive_installs_disables_and_restarts_without_model(tmp_path):
    builder = script('build-module.py')
    verifier = script('verify_installed.py')
    archive = builder.archive(tmp_path / 'workspace.zip')
    result = verifier.workspace_smoke(tmp_path / 'root', archive, '1.5.0')
    assert result['disable_and_restart_verified'] is True
    assert result['enabled_skill_count'] == 7
