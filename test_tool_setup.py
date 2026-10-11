"""Offline regressions for user-selected tool locations and safe setup ordering."""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import easyki_setup as easyki
import environment_setup as environment
import tool_locations as locations


class ToolSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ad2kicad-tools-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codex = self.root / 'codex'
        self.codex.mkdir()
        self.fake_cli = self.root / 'bin' / 'kicad-cli.exe'
        self.fake_cli.parent.mkdir()
        self.fake_cli.touch()
        self.fake_cli.with_name('python.exe').touch()

    def options(self, **values):
        args = dict(kicad_cli=self.fake_cli, workspace=self.root,
                    output=self.root / 'audit', codex_root=self.codex,
                    mcp_root=None, mcp_config_root=None, easyki_root=None,
                    easyki_config_root=None, smoke_schematic=None)
        return Namespace(**(args | values))

    def mcp(self, name='service'):
        root = self.root / name
        (root / 'python').mkdir(parents=True)
        (root / 'package.json').write_text('{}', encoding='utf-8')
        (root / 'python/kicad_interface.py').touch()
        return root

    def test_missing_tools_require_selection_before_installation(self):
        with patch.object(locations, '_easy_candidates', return_value=[]), \
             patch.object(environment, 'run', return_value=SimpleNamespace(stdout='10.0')), \
             patch.object(environment, 'check_happy') as happy, \
             patch.object(environment, 'ensure_mcp') as mcp, \
             patch.object(environment, 'ensure_easyki') as app, redirect_stdout(io.StringIO()):
            self.assertEqual(environment.setup(self.options()), 1)
        happy.assert_not_called()
        mcp.assert_not_called()
        app.assert_not_called()
        self.assertFalse((self.codex / 'config.toml').exists())
        state = json.loads((self.root / 'audit/environment-state.json').read_text(encoding='utf-8'))
        self.assertIn('Select custom', state['error'])

    def test_missing_config_directory_is_rejected_before_installation(self):
        with patch.object(environment, 'run', return_value=SimpleNamespace(stdout='10.0')), \
             patch.object(environment, 'check_happy') as happy, redirect_stdout(io.StringIO()):
            result = environment.setup(self.options(mcp_root=self.root / 'new service',
                easyki_root=self.root / 'new converter', easyki_config_root=self.root / 'app config'))
        self.assertEqual(result, 1)
        happy.assert_not_called()
        self.assertFalse((self.root / 'new service').exists())
        self.assertFalse((self.root / 'new converter').exists())

    def test_new_root_does_not_inherit_another_installation_config(self):
        old = self.mcp('old service')
        record = {'version': 1, 'mcp': {'root': str(old), 'config_root': str(self.root / 'old config')},
                  'easyki': {}}
        (self.codex / locations.RECORD_NAME).write_text(json.dumps(record), encoding='utf-8')
        with patch.object(locations, '_easy_candidates', return_value=[]):
            result = locations.inspect_tool_locations(self.codex, {}, mcp_root=self.root / 'new service')
        self.assertIsNone(result['mcp']['config_root'])

    def test_nested_easy_installation_is_reused(self):
        root = self.root / '\u5de5\u5177 [test] & app'
        executable = root / 'bin' / 'EasyKiConverter.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        value = locations.inspect_tool_locations(self.codex, {}, easyki_root=root,
                                                easyki_config_root=self.root / 'preferences')
        self.assertTrue(value['easyki']['installed'])
        self.assertEqual(Path(value['easyki']['root']), executable.parent)

    def test_unknown_nonempty_installation_directory_is_preserved(self):
        directory = self.root / 'user files'
        directory.mkdir()
        marker = directory / 'important.txt'
        marker.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            locations.inspect_tool_locations(self.codex, {}, mcp_root=directory)
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_custom_mcp_config_preserves_existing_bytes(self):
        root = self.mcp()
        config = self.root / '\u914d\u7f6e [custom]' / 'config.json'
        config.parent.mkdir()
        original = b'{"logLevel":"debug", "customPreference": 27}\n'
        config.write_bytes(original)
        result = environment.configure_mcp_file({'config_path': str(config)}, root,
                    self.fake_cli.with_name('python.exe'), self.fake_cli)
        self.assertFalse(result['config_created'])
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(Path(result['config_path']), config)

    def test_new_mcp_config_is_written_to_selected_location(self):
        folder = self.root / 'chosen config'
        result = environment.configure_mcp_file({'config_root': str(folder)}, self.mcp(),
                    self.fake_cli.with_name('python.exe'), self.fake_cli)
        self.assertTrue(result['config_created'])
        self.assertEqual(Path(result['config_path']), folder / 'config.json')
        actual = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(actual['kicadPath'], str(self.fake_cli))

    def test_unsafe_zip_paths_are_rejected_before_extraction(self):
        for index, name in enumerate(('../escape.txt', 'C:/escape.txt', 'folder/NUL.txt', 'file:stream')):
            archive = self.root / f'bad-{index}.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr(name, b'unsafe')
            with self.assertRaises(RuntimeError):
                easyki.safe_extract(archive, self.root / f'extracted-{index}')
            self.assertFalse((self.root / f'extracted-{index}').exists())

    def test_arm64_has_no_silent_x64_substitution(self):
        release = {'tag_name': 'fixture', 'assets': [{'name': 'EasyKiConverter-1-win64.zip', 'size': 10}]}
        with self.assertRaisesRegex(RuntimeError, 'no unique Windows arm64'):
            easyki.select_asset(release, 'arm64')


if __name__ == '__main__':
    unittest.main(verbosity=2)
