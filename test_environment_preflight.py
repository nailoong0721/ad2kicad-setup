"""Regressions for cold-machine setup, retry choices and early path checks."""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import environment_setup as environment
import tool_locations as locations


class EnvironmentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ad2kicad-preflight-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.codex = self.root / 'codex'
        self.output = self.root / 'audit'
        self.plan = {name: {'root': str(self.root / (name + ' program')),
                           'config_root': str(self.root / (name + ' config')),
                           'installed': False, 'needs_directory': False}
                     for name in ('node', 'mcp', 'easyki')}

    def test_empty_explicit_node_directory_is_a_completed_selection(self):
        with patch.object(locations, '_easy_candidates', return_value=[]):
            found = locations.inspect_tool_locations(self.codex, {}, node_root=self.root / 'new node',
                                                     node_config_root=self.root / 'node config')
        self.assertFalse(found['node']['needs_directory'])
        self.assertFalse(found['node']['installed'])
        self.assertEqual(found['node']['root'], str(self.root / 'new node'))

    def test_shared_configuration_is_rejected_before_creation(self):
        self.plan['easyki']['config_root'] = self.plan['mcp']['config_root']
        with self.assertRaisesRegex(ValueError, 'Configuration directories'):
            locations.validate_tool_plan(self.plan, self.output)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stale_saved_installations_do_not_block_the_wizard(self):
        old_mcp = self.root / 'old mcp'
        old_easy = self.root / 'old easy'
        for path in (old_mcp, old_easy):
            path.mkdir()
            (path / 'user.txt').write_bytes(b'preserve')
        self.codex.mkdir()
        record = {'version': 1, 'mcp': {'root': str(old_mcp)}, 'easyki': {'root': str(old_easy)}}
        (self.codex / locations.RECORD_NAME).write_text(json.dumps(record), encoding='utf-8')
        with patch.object(locations, '_easy_candidates', return_value=[]):
            found = locations.inspect_tool_locations(self.codex, {})
        for tool, old in (('mcp', old_mcp), ('easyki', old_easy)):
            self.assertTrue(found[tool]['needs_directory'])
            self.assertEqual(found[tool]['rejected_saved_root'], str(old))
            self.assertEqual((old / 'user.txt').read_bytes(), b'preserve')

    @unittest.skipUnless(os.name == 'nt', 'Windows path case semantics')
    def test_executable_case_does_not_rewrite_remembered_paths(self):
        mcp = {'root': str(self.root / 'mcp'), 'config_root': str(self.root / 'mcp config')}
        easy = {'root': str(self.root / 'easy'), 'executable': str(self.root / 'easy/easykiconverter.exe')}
        node = {'root': str(self.root / 'node'), 'node': str(self.root / 'node/node.exe')}
        locations.remember_tool_locations(self.codex, mcp, easy, node)
        before = (self.codex / locations.RECORD_NAME).read_bytes()
        easy['executable'] = str(self.root / 'easy/EasyKiConverter.exe')
        result = locations.remember_tool_locations(self.codex, mcp, easy, node)
        self.assertFalse(result['changed'])
        self.assertEqual(before, (self.codex / locations.RECORD_NAME).read_bytes())

    def test_nested_programs_are_rejected_before_creation(self):
        self.plan['easyki']['root'] = str(Path(self.plan['mcp']['root']) / 'converter')
        with self.assertRaisesRegex(ValueError, 'Program directories'):
            locations.validate_tool_plan(self.plan, self.output)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_config_inside_new_installation_is_rejected(self):
        self.plan['node']['config_root'] = str(Path(self.plan['node']['root']) / 'config')
        with self.assertRaisesRegex(ValueError, 'configuration must be outside'):
            locations.validate_tool_plan(self.plan, self.output)

    def test_config_inside_existing_own_program_is_preserved(self):
        root = Path(self.plan['mcp']['root'])
        config = root / 'config'
        config.mkdir(parents=True)
        (config / 'config.json').write_text('{}', encoding='utf-8')
        self.plan['mcp'].update(installed=True, config_root=str(config))
        self.assertTrue(locations.validate_tool_plan(self.plan, self.output)['passed'])
        self.assertEqual((config / 'config.json').read_bytes(), b'{}')

    def test_readonly_plan_probe_leaves_no_directories(self):
        self.assertTrue(locations.validate_tool_plan(self.plan, self.output)['passed'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_pip_bootstraps_selected_interpreter_only(self):
        selected = self.root / 'KiCad Python/python.exe'
        with patch.object(environment, 'run', side_effect=[
            SimpleNamespace(returncode=1, stderr='No module named pip'),
            SimpleNamespace(returncode=0, stderr=''),
            SimpleNamespace(returncode=0, stderr='')]) as run:
            self.assertTrue(environment.ensure_pip(selected))
        self.assertEqual(run.call_args_list[1].args[0], [selected, '-m', 'ensurepip', '--user'])

    def test_failed_pip_bootstrap_has_an_actionable_error(self):
        with patch.object(environment, 'run', side_effect=[
            SimpleNamespace(returncode=1, stderr='No pip'),
            SimpleNamespace(returncode=1, stderr='No ensurepip')]):
            with self.assertRaisesRegex(RuntimeError, 'Repair/reinstall'):
                environment.ensure_pip(self.root / 'python.exe')

    def test_choices_survive_a_download_failure_without_false_success(self):
        cli = self.root / 'KiCad/bin/kicad-cli.exe'
        cli.parent.mkdir(parents=True)
        cli.touch()
        cli.with_name('python.exe').touch()
        (cli.parent.parent / 'share/kicad/footprints').mkdir(parents=True)
        args = Namespace(kicad_cli=cli, workspace=self.root, output=self.output,
                         codex_root=self.codex, smoke_schematic=None)
        with patch.object(environment, 'inspect_tool_locations', return_value=self.plan), \
             patch.object(environment, 'run', return_value=SimpleNamespace(stdout='10.0')), \
             patch.object(environment, 'ensure_node', side_effect=RuntimeError('simulated download unavailable')), \
             patch.object(environment, 'check_happy') as happy, redirect_stdout(io.StringIO()):
            self.assertEqual(environment.setup(args), 1)
        happy.assert_not_called()
        saved = json.loads((self.codex / locations.RECORD_NAME).read_text(encoding='utf-8'))
        self.assertEqual(saved['node']['root'], self.plan['node']['root'])
        self.assertEqual(saved['node']['config_root'], self.plan['node']['config_root'])
        state = json.loads((self.output / 'environment-state.json').read_text(encoding='utf-8'))
        self.assertFalse(state['passed'])
        self.assertEqual(state['stage'], 'node')
        self.assertIn('download unavailable', state['error'])
        self.assertFalse((self.codex / 'config.toml').exists())

    def test_portable_node_is_on_npm_subprocess_path(self):
        root = self.root / 'mcp'
        (root / 'python').mkdir(parents=True)
        (root / 'python/kicad_interface.py').touch()
        (root / 'package.json').write_text('{"version":"fixture","scripts":{"build":"tsc","prepare":"npm run build"}}', encoding='utf-8')
        (root / 'package-lock.json').write_text('{"packages":{}}', encoding='utf-8')
        compiler = root / 'node_modules/typescript/bin/tsc'
        compiler.parent.mkdir(parents=True)
        compiler.touch()
        node = self.root / 'portable node/node.exe'
        node.parent.mkdir()
        node.touch()
        npm = node.with_name('npm.cmd')
        npm.touch()
        npm_cli = node.parent / 'node_modules/npm/bin/npm-cli.js'
        npm_cli.parent.mkdir(parents=True)
        npm_cli.touch()
        info = {'node': str(node), 'npm': str(npm), 'npm_cli': str(npm_cli),
                'npm_user_config': str(self.root / 'config/npmrc'),
                'npm_cache': str(self.root / 'config/cache'), 'config_root': str(self.root / 'config'),
                'env': {'NPM_CONFIG_PREFIX': str(self.root / 'config/global')}}
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if '--version' in argv:
                return SimpleNamespace(stdout='v24.1.0', returncode=0)
            return SimpleNamespace(stdout='', returncode=1 if '--input-type=module' in argv else 0)
        self.output.mkdir()
        status = {'passed': True, 'modules': {'pcbnew': {'passed': True}}}
        with patch.object(environment, 'run', side_effect=run), \
             patch.object(environment, 'probe_python', return_value=status), \
             patch.dict(os.environ, {'PATH': str(self.root / 'no node on path')}):
            environment.ensure_mcp(root, self.root / 'python.exe', self.output, info)
        npm_calls = [kwargs for argv, kwargs in calls if len(argv) > 1 and argv[1] in (npm_cli, compiler)]
        self.assertEqual(len(npm_calls), 2)
        for kwargs in npm_calls:
            self.assertEqual(kwargs['env']['PATH'].split(os.pathsep)[0], str(node.parent))
            self.assertEqual(kwargs['env']['NPM_CONFIG_PREFIX'], info['env']['NPM_CONFIG_PREFIX'])
        install = next(argv for argv, _ in calls if len(argv) > 1 and argv[1] == npm_cli)
        self.assertIn('--ignore-scripts', install)
        self.assertIn('--include=dev', install)
        self.assertTrue(any(argv == [node, compiler] for argv, _ in calls))

    def test_changed_upstream_lifecycle_stops_before_installation(self):
        (self.root / 'package.json').write_text(json.dumps({'scripts': {'build': 'tsc', 'prepublish': 'custom hook'}}), encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'different build/install workflow'):
            environment.validate_mcp_build(self.root)

    def test_runtime_install_hooks_are_not_silently_skipped(self):
        (self.root / 'package.json').write_text('{"scripts":{"build":"tsc"}}', encoding='utf-8')
        (self.root / 'package-lock.json').write_text(json.dumps({'packages': {
            'node_modules/dev-only': {'dev': True, 'hasInstallScript': True},
            'node_modules/runtime-addon': {'hasInstallScript': True}}}), encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'runtime-addon'):
            environment.validate_mcp_build(self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
