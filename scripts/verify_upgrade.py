"""Check the real folder-module upgrade with isolated files and fake embeddings."""
import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'package')]
from contextopus.startup import Application
from contextopus.core.resources import ProcessResources
from test_runtime import Factory
from workspace.configuration import WORKSPACE_CREATION_DEFAULTS

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--previous-module', type=Path, required=True)
parser.add_argument('--workspace-module', type=Path, required=True)
args = parser.parse_args()
old = str(args.previous_module.resolve())
new = str(args.workspace_module.resolve())
with tempfile.TemporaryDirectory(prefix='workspace-reservation-upgrade-') as temporary:
    root = Path(temporary) / 'root'
    with zipfile.ZipFile(new) as archive:
        archive.extractall(Path(temporary) / 'candidate')
    new = str(Path(temporary) / 'candidate/workspace')
    with ProcessResources(Factory()) as resources, Application(root, 'test', resources=resources, discover=False) as app:
        source = app.query('core.module_source_plan', {'module': 'workspace', 'source': old})
        assert app.execute('core.module_install', {'module': 'workspace', 'source': old, 'source_revision': source['revision']})['state'] == 'enabled'
        try:
            app.query('describe', {'operation': 'workspace.reserve'})
        except ValueError:
            pass
        else:
            raise AssertionError('Previous module unexpectedly contains edit reservations')
        app.execute('workspace.workspace_create', {
            'request_id': 'create', 'user_request': 'Create isolated upgrade acceptance context',
            'instructions_content': '# Rules\nKeep verified information.',
            'project_content': '# Project\nCheck safe module updates.',
            'project_description': 'Isolated upgrade acceptance fixture.',
            'settings': {**WORKSPACE_CREATION_DEFAULTS, 'knowledge_approval_required': False},
        })
        cycle = app.execute('workspace.startup', {'request_id': 'old-work', 'user_request': 'Retain this work'})['cycle_id']
        project = app.query('workspace.project_read')
        pending = app.execute('workspace.proposal_prepare', {'cycle_id': cycle, 'changes': [{
            'material_id': project['id'], 'expected_revision': project['revision'], 'status': 'verified'}]})
        paths = list((root / 'modules/workspace/workspace_contexts/test').rglob('*.md'))
        before = {p.relative_to(root): p.read_bytes() for p in paths}
        updated = app.execute('core.module_update', {'module': 'workspace', 'source': new})
        assert updated['state'] == 'enabled', updated
        assert all((root / p).read_bytes() == content for p, content in before.items())
        assert app.query('workspace.proposal_read', {'cycle_id': cycle, 'proposal_id': pending['proposal_id']})['revision'] == pending['revision']
        assert app.execute('workspace.proposal_apply', {'cycle_id': cycle, 'proposal_id': pending['proposal_id'], 'expected_revision': pending['revision']})['status'] == 'applied'
        # Replacing module code preserves format 5. The reviewed context migration
        # is a separate operation and must precede starting work on format 6.
        migration = app.query('workspace.upgrade_plan')
        assert not migration['compatible']
        app.execute('workspace.upgrade_apply', {
            'expected_revision': migration['revision'],
            'user_request': 'Migrate this isolated upgrade verification context',
        })
        assert app.query('workspace.upgrade_plan')['compatible']
        assert 'Keep verified information.' in app.query('workspace.instructions_read')['content']
        other = app.execute('workspace.startup', {'request_id': 'new-work', 'user_request': 'Independent work'})['cycle_id']
        lease = app.execute('workspace.reserve', {'cycle_id': cycle, 'material_ids': [project['id']], 'reason': 'Upgrade acceptance edit'})
        assert app.query('workspace.read', {'material_id': project['id']})['status'] == 'verified'
        try:
            app.execute('workspace.reserve', {'cycle_id': other, 'material_ids': [project['id']], 'reason': 'Competing edit'})
        except ValueError as exc:
            assert 'Upgrade acceptance edit' in str(exc), str(exc)
        else:
            raise AssertionError('Competing reservation succeeded')
    with ProcessResources(Factory()) as resources, Application(root, 'test', resources=resources, discover=False) as app:
        assert app.query('workspace.reservations')['total'] == 1
        assert app.query('workspace.read', {'material_id': project['id']})['status'] == 'verified'
    print(json.dumps({'old_version': '1.5.0', 'new_version': '1.7.0', 'module_update': 'passed',
                      'unchanged_source_bytes': len(before), 'pending_review_preserved': True,
                      'legacy_proposal_applied': True, 'competing_write_blocked': True,
                      'reads_available': True, 'ownership_survives_restart': True}))
