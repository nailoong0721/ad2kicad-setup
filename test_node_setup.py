"""Node setup failure boundaries; no network or user installations are changed."""
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import node_setup as node


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 29)


class NodeSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "Node runtime"
        self.config = self.base / "custom \u914d\u7f6e # folder"
        self.output = self.base / "reports"
        self.patch(node, "date", FixedDate)
        self.patch(node, "_host_arch", return_value="x64")

    def patch(self, obj, name, *args, **kwargs):
        value = patch.object(obj, name, *args, **kwargs)
        self.addCleanup(value.stop)
        return value.start()

    def fixture_runtime(self, root=None):
        root = root or self.root
        (root / "node_modules/npm/bin").mkdir(parents=True)
        (root / "node.exe").write_bytes(b"node fixture")
        (root / "npm.cmd").write_bytes(b"npm fixture")
        (root / "node_modules/npm/bin/npm-cli.js").write_bytes(b"npm cli fixture")
        return root

    def successful_run(self, executable, arguments, **kwargs):
        if arguments[0] == "-p":
            stdout = json.dumps({"version": "v24.21.0", "platform": "win32", "arch": "x64", "lts": "Krypton"})
        elif arguments[-1] == "--version":
            stdout = "11.6.0"
        else:
            stdout = kwargs["env"]["NPM_CONFIG_" + arguments[-1].upper()]
        return subprocess.CompletedProcess([], 0, stdout + "\n", "")

    def archive(self, entries=None):
        path = self.base / "node-v24.21.0-win-x64.zip"
        root = path.stem
        with zipfile.ZipFile(path, "w") as bundle:
            for name, contents in entries or [(root + "/node.exe", b"node")]:
                bundle.writestr(name, contents)
        return path

    def test_explicit_missing_runtime_never_falls_back_to_path(self):
        lookup = self.patch(node.shutil, "which", return_value="C:/other/node.exe")
        info = node.inspect_node(self.root)
        self.assertFalse(info["installed"])
        self.assertEqual(info["source"], "explicit")
        self.assertEqual(info["root"], str(self.root))
        lookup.assert_not_called()

    def test_both_node_and_npm_must_work(self):
        self.fixture_runtime()
        run = self.patch(node, "_run", side_effect=self.successful_run)
        self.assertTrue(node.inspect_node(self.root)["installed"])
        self.assertEqual(run.call_count, 2)
        run.side_effect = [self.successful_run(None, ["-p"]), subprocess.CompletedProcess([], 1, "", "broken npm")]
        info = node.inspect_node(self.root)
        self.assertFalse(info["installed"])
        self.assertIn("broken npm", info["reason"])

    def test_missing_npm_cli_is_not_healthy(self):
        self.fixture_runtime()
        (self.root / "node_modules/npm/bin/npm-cli.js").unlink()
        self.patch(node, "_run", side_effect=self.successful_run)
        self.assertFalse(node.inspect_node(self.root)["installed"])

    def test_timeout_is_reported_as_unhealthy(self):
        self.fixture_runtime()
        self.patch(node, "_run", side_effect=subprocess.TimeoutExpired("node", 20))
        self.assertIn("timed out", node.inspect_node(self.root)["reason"])

    def test_eol_current_and_incompatible_arch_are_rejected(self):
        self.fixture_runtime()
        for version, lts, arch in (("v20.20.0", "Iron", "x64"), ("v26.9.0", False, "x64"),
                                   ("v24.21.0", "Krypton", "arm64")):
            with self.subTest(version=version, arch=arch):
                value = json.dumps({"version": version, "lts": lts, "arch": arch, "platform": "win32"})
                with patch.object(node, "_run", return_value=subprocess.CompletedProcess([], 0, value, "")):
                    self.assertFalse(node.inspect_node(self.root)["installed"])

    def test_index_selects_latest_supported_arch_including_security_release(self):
        releases = [{"version": "v24.20.0", "lts": "Krypton", "security": False, "files": ["win-x64-zip"]},
                    {"version": "v26.9.0", "lts": False, "files": ["win-x64-zip"]},
                    {"version": "v24.22.0", "lts": "Krypton", "security": True, "files": ["win-arm64-zip"]},
                    {"version": "v24.21.0", "lts": "Krypton", "security": True, "files": ["win-x64-zip"]}]
        self.patch(node, "_read_official", return_value=json.dumps(releases).encode())
        self.assertEqual(node._latest_release()[0], "v24.21.0")
        with patch.object(node, "_host_arch", return_value="arm64"):
            self.assertEqual(node._latest_release()[1], "node-v24.22.0-win-arm64.zip")

    def test_failed_download_cleans_partial_without_replacing_existing_file(self):
        target = self.base / "archive.zip"
        target.write_bytes(b"user original")
        response = io.BytesIO(b"short")
        response.headers = {"Content-Length": "100"}
        self.patch(node.urllib.request, "urlopen", return_value=response)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            node._download("https://nodejs.org/test.zip", target)
        self.assertEqual(target.read_bytes(), b"user original")
        self.assertEqual(list(self.base.glob("*.partial-*")), [])

    def test_checksum_mismatch_never_installs_or_deletes_old_cache(self):
        cache = self.base / "cache"
        cache.mkdir()
        name = "node-v24.21.0-win-x64.zip"
        original = cache / name
        original.write_bytes(b"user archive")
        self.patch(node, "_read_official", return_value=(("0" * 64) + "  " + name + "\n").encode())
        self.patch(node, "_download", side_effect=lambda url, path: path.write_bytes(b"bad download"))
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            node._official_archive("v24.21.0", name, node.NODE_DIST + "v24.21.0/", cache)
        self.assertEqual(original.read_bytes(), b"user archive")
        self.assertEqual(list(cache.glob("download-*/*.zip")), [])

    def test_download_total_deadline_stops_a_trickling_response(self):
        target = self.base / "archive.zip"
        response = io.BytesIO(b"still streaming")
        response.headers = {}
        self.patch(node.urllib.request, "urlopen", return_value=response)
        self.patch(node.time, "monotonic", side_effect=[0, node.DOWNLOAD_TIMEOUT + 1])
        with self.assertRaisesRegex(RuntimeError, "10-minute timeout"):
            node._download("https://nodejs.org/test.zip", target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.base.glob("*.partial-*")), [])

    def test_valid_cache_is_reused_without_download(self):
        name = "node-v24.21.0-win-x64.zip"
        contents = b"verified existing archive"
        archive = self.base / name
        archive.write_bytes(contents)
        self.patch(node, "_read_official", return_value=(hashlib.sha256(contents).hexdigest() + "  " + name + "\n").encode())
        download = self.patch(node, "_download")
        self.assertEqual(node._official_archive("v24.21.0", name, node.NODE_DIST + "v24.21.0/", self.base), archive)
        download.assert_not_called()

    def test_unsafe_zip_does_not_publish_or_escape(self):
        root = "node-v24.21.0-win-x64"
        for unsafe in (root + "/../escape", root + "/C:/escape", root + "/AUX.txt", root + "/bad. /file"):
            with self.subTest(member=unsafe):
                archive = self.archive([(unsafe, b"escape")])
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    node._extract(archive, self.root, "v24.21.0")
                self.assertFalse(self.root.exists())
                self.assertFalse((self.base / "escape").exists())
                self.assertEqual(list(self.base.glob(".node-stage-*")), [])

    def test_zip_case_duplicates_and_symlinks_are_rejected(self):
        root = "node-v24.21.0-win-x64"
        archive = self.archive([(root + "/node.exe", b"one"), (root + "/NODE.EXE", b"two")])
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            node._extract(archive, self.root, "v24.21.0")
        link = zipfile.ZipInfo(root + "/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.archive([(link, b"../escape")])
        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            node._extract(archive, self.root, "v24.21.0")

    def test_failed_staged_health_preserves_empty_destination(self):
        self.root.mkdir()
        archive = self.archive()
        self.patch(node, "inspect_node", return_value={"installed": False, "reason": "bad executable"})
        with self.assertRaisesRegex(RuntimeError, "bad executable"):
            node._extract(archive, self.root, "v24.21.0")
        self.assertTrue(self.root.is_dir())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(list(self.base.glob(".node-stage-*")), [])

    def test_validated_runtime_is_published(self):
        archive = self.archive()
        self.patch(node, "inspect_node", return_value={"installed": True, "version": "v24.21.0", "reason": "ok"})
        self.assertEqual(node._extract(archive, self.root, "v24.21.0"), self.root / "node.exe")
        self.assertEqual((self.root / "node.exe").read_bytes(), b"node")

    def test_transient_publish_permission_error_is_retried(self):
        archive = self.archive()
        self.patch(node, "inspect_node", return_value={"installed": True, "version": "v24.21.0", "reason": "ok"})
        original_rename = Path.rename
        attempts = []
        def rename(stage, destination):
            attempts.append(stage)
            if len(attempts) < 3:
                raise PermissionError(13, "Transient Windows executable lock")
            return original_rename(stage, destination)
        sleep = self.patch(node.time, "sleep")
        with patch.object(Path, "rename", rename):
            node._extract(archive, self.root, "v24.21.0")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2])
        self.assertEqual((self.root / "node.exe").read_bytes(), b"node")
        self.assertEqual(list(self.base.glob(".node-stage-*")), [])

    def test_permanent_publish_permission_error_preserves_verified_stage(self):
        archive = self.archive()
        self.patch(node, "inspect_node", return_value={"installed": True, "version": "v24.21.0", "reason": "ok"})
        self.patch(node.time, "monotonic", side_effect=[0, 1, 5])
        sleep = self.patch(node.time, "sleep")
        with patch.object(Path, "rename", side_effect=PermissionError(13, "Locked")) as rename:
            with self.assertRaisesRegex(RuntimeError, "verified files are preserved at") as failure:
                node._extract(archive, self.root, "v24.21.0")
        self.assertEqual(rename.call_count, 2)
        sleep.assert_called_once_with(0.1)
        stages = list(self.base.glob(".node-stage-*"))
        self.assertEqual(len(stages), 1)
        self.assertIn(str(stages[0]), str(failure.exception))
        self.assertEqual((stages[0] / "node.exe").read_bytes(), b"node")
        self.assertFalse(self.root.exists())

    def test_publish_recheck_preserves_files_created_during_retry(self):
        archive = self.archive()
        self.patch(node, "inspect_node", return_value={"installed": True, "version": "v24.21.0", "reason": "ok"})
        def concurrent_writer(delay):
            self.root.mkdir()
            (self.root / "another process.txt").write_bytes(b"preserve")
        self.patch(node.time, "sleep", side_effect=concurrent_writer)
        with patch.object(Path, "rename", side_effect=PermissionError(13, "Transient lock")) as rename:
            with self.assertRaisesRegex(RuntimeError, "not empty"):
                node._extract(archive, self.root, "v24.21.0")
        rename.assert_called_once()
        self.assertEqual((self.root / "another process.txt").read_bytes(), b"preserve")
        self.assertEqual(len(list(self.base.glob(".node-stage-*"))), 1)

    def test_nonpermission_publish_error_is_not_retried(self):
        archive = self.archive()
        self.patch(node, "inspect_node", return_value={"installed": True, "version": "v24.21.0", "reason": "ok"})
        sleep = self.patch(node.time, "sleep")
        with patch.object(Path, "rename", side_effect=FileExistsError(17, "Concurrent directory")) as rename:
            with self.assertRaisesRegex(RuntimeError, "Concurrent directory"):
                node._extract(archive, self.root, "v24.21.0")
        rename.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(len(list(self.base.glob(".node-stage-*"))), 1)

    def test_bad_nonempty_installation_is_preserved_before_network(self):
        self.root.mkdir()
        sentinel = self.root / "my files.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        latest = self.patch(node, "_latest_release")
        with self.assertRaisesRegex(RuntimeError, "not empty"):
            node.ensure_node(self.root, self.config, self.output)
        latest.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_existing_runtime_custom_unicode_config_reuses_and_preserves_npmrc(self):
        self.fixture_runtime()
        self.config.mkdir()
        original = b"# user's custom settings\nregistry=https://registry.npmjs.org/\n"
        (self.config / "npmrc").write_bytes(original)
        self.patch(node, "_run", side_effect=self.successful_run)
        latest = self.patch(node, "_latest_release")
        result = node.ensure_node(self.root, self.config, self.output)
        latest.assert_not_called()
        self.assertFalse(result["just_installed"])
        self.assertEqual(result["npm_prefix"], str(self.config / "prefix"))
        self.assertEqual(result["env"]["NPM_CONFIG_USERCONFIG"], str(self.config / "npmrc"))
        self.assertEqual((self.config / "npmrc").read_bytes(), original)
        self.assertTrue((self.output / "node-state.json").is_file())

    def test_npm_config_must_actually_use_selected_paths(self):
        self.fixture_runtime()
        def mismatched(executable, arguments, **kwargs):
            if "config" in arguments:
                return subprocess.CompletedProcess([], 0, str(self.base / "wrong"), "")
            return self.successful_run(executable, arguments, **kwargs)
        self.patch(node, "_run", side_effect=mismatched)
        with self.assertRaisesRegex(RuntimeError, "selected cache"):
            node.ensure_node(self.root, self.config, self.output)
        self.assertFalse((self.output / "node-state.json").exists())

    def test_generated_config_quotes_custom_paths_and_sets_prefix(self):
        env = node._configure_npm(self.config)
        contents = (self.config / "npmrc").read_text(encoding="utf-8")
        self.assertIn('prefix="', contents)
        self.assertIn("\u914d\u7f6e # folder", contents)
        self.assertTrue(Path(env["NPM_CONFIG_PREFIX"]).is_dir())

    def test_missing_root_requires_selection(self):
        with self.assertRaisesRegex(RuntimeError, "Select a Node.js"):
            node.ensure_node(None, self.config, self.output)


if __name__ == "__main__":
    unittest.main()
