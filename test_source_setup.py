"""Offline source acquisition regressions; writes stay in temporary fixtures."""
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import source_setup as setup


COMMIT = "a" * 40


class SourceSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ad2kicad-source-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "chosen source"

    def source_files(self, root, url=setup.MCP_URL):
        for relative in setup.REPOSITORIES[url][1]:
            file = root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture\n", encoding="utf-8")

    def archive_bytes(self, extra=None, incomplete=False):
        data = io.BytesIO()
        prefix = "KiCAD-MCP-Server-" + COMMIT
        with zipfile.ZipFile(data, "w") as archive:
            required = setup.REPOSITORIES[setup.MCP_URL][1]
            for relative in (required[:1] if incomplete else required):
                archive.writestr(prefix + "/" + relative, "fixture\n")
            if extra:
                archive.writestr(extra.replace("{root}", prefix), "unsafe")
        return data.getvalue()

    def download(self, data):
        def write(repository, commit, destination):
            destination.write_bytes(data)
            return f"https://codeload.github.com/{repository}/zip/{commit}"
        return write

    def install_zip(self, data):
        with patch.object(setup.shutil, "which", return_value=None), \
             patch.object(setup, "_latest_commit", return_value=COMMIT), \
             patch.object(setup, "_download_archive", side_effect=self.download(data)):
            return setup.ensure_source(setup.MCP_URL, self.target)

    def test_existing_zip_source_reused_without_git_or_writes(self):
        self.source_files(self.target)
        marker = self.target / "keep.txt"
        marker.write_bytes(b"keep")
        with patch.object(setup.shutil, "which") as which, patch.object(setup, "_latest_commit") as fetch:
            self.assertFalse(setup.ensure_source(setup.MCP_URL, self.target))
        which.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertFalse((self.target / setup.SOURCE_RECORD).exists())

    def test_git_directory_alone_does_not_identify_healthy_source(self):
        (self.target / ".git").mkdir(parents=True)
        with patch.object(setup.shutil, "which") as which, self.assertRaisesRegex(RuntimeError, "preserved"):
            setup.ensure_source(setup.MCP_URL, self.target)
        which.assert_not_called()
        self.assertTrue((self.target / ".git").is_dir())

    def test_unknown_nonempty_destination_is_preserved(self):
        self.target.mkdir()
        marker = self.target / "important.txt"
        marker.write_bytes(b"user data")
        with self.assertRaisesRegex(RuntimeError, "preserved"):
            self.install_zip(self.archive_bytes())
        self.assertEqual(marker.read_bytes(), b"user data")

    def test_only_official_repository_urls_are_accepted(self):
        with self.assertRaisesRegex(ValueError, "official"):
            setup.ensure_source("https://github.com/other/repository.git", self.target)
        self.assertFalse(self.target.exists())

    def test_no_git_install_pins_commit_and_records_local_integrity(self):
        self.target = self.root / "\u4e2d\u6587 [folder] & source"
        self.target.mkdir()
        self.assertTrue(self.install_zip(self.archive_bytes()))
        record = json.loads((self.target / setup.SOURCE_RECORD).read_text(encoding="utf-8"))
        self.assertEqual(record["commit"], COMMIT)
        self.assertEqual(record["repository"], "mixelpixx/KiCAD-MCP-Server")
        self.assertTrue(record["download_url"].endswith("/zip/" + COMMIT))
        self.assertEqual(len(record["archive_sha256"]), 64)
        self.assertEqual(record["source_tree_sha256"], setup._tree_sha256(self.target))
        self.assertEqual(record["method"], "github_commit_zip")
        self.assertIn("no upstream signature", record["integrity_note"])
        self.assertEqual(list(self.root.iterdir()), [self.target])
        before = (self.target / setup.SOURCE_RECORD).read_bytes()
        self.assertFalse(setup.ensure_source(setup.MCP_URL, self.target))
        self.assertEqual((self.target / setup.SOURCE_RECORD).read_bytes(), before)

    def test_bad_archives_never_publish_destination(self):
        for extra in ("{root}/../escape.txt", "{root}/NUL.txt", "another-root/file.txt",
                      "{root}/file:stream", "{root}/PACKAGE.JSON"):
            with self.subTest(extra=extra), self.assertRaisesRegex(RuntimeError, "staging evidence"):
                self.install_zip(self.archive_bytes(extra=extra))
            self.assertFalse(self.target.exists())
            self.assertFalse((self.root / "escape.txt").exists())

    def test_incomplete_sources_never_publish_destination(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.install_zip(self.archive_bytes(incomplete=True))
        self.assertFalse(self.target.exists())

    def test_symbolic_link_member_never_publishes_destination(self):
        data = io.BytesIO(self.archive_bytes())
        with zipfile.ZipFile(data, "a") as archive:
            member = zipfile.ZipInfo("KiCAD-MCP-Server-" + COMMIT + "/linked-file")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "../../user-data")
        with self.assertRaisesRegex(RuntimeError, "Unsafe ZIP"):
            self.install_zip(data.getvalue())
        self.assertFalse(self.target.exists())

    def test_oversized_archive_rejected_before_extract(self):
        with patch.object(setup, "MAX_EXPANDED_BYTES", 1), self.assertRaisesRegex(RuntimeError, "expanded size"):
            self.install_zip(self.archive_bytes())
        self.assertFalse(self.target.exists())

    def happy_archive(self, value="../../skills/bom", include_target=True):
        data = io.BytesIO()
        prefix = "kicad-happy-" + COMMIT
        with zipfile.ZipFile(data, "w") as archive:
            for relative in setup.REPOSITORIES[setup.HAPPY_URL][1]:
                if not include_target and relative == "skills/bom/SKILL.md":
                    continue
                archive.writestr(prefix + "/" + relative, "fixture\n")
            alias = zipfile.ZipInfo(prefix + "/.agents/skills/bom")
            alias.create_system = 3
            alias.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(alias, value)
        return data.getvalue()

    def test_upstream_skill_alias_is_omitted_and_real_skill_is_installed(self):
        with patch.object(setup.shutil, "which", return_value=None), \
             patch.object(setup, "_latest_commit", return_value=COMMIT), \
             patch.object(setup, "_download_archive", side_effect=self.download(self.happy_archive())):
            self.assertTrue(setup.ensure_source(setup.HAPPY_URL, self.target))
        self.assertTrue((self.target / "skills/bom/SKILL.md").is_file())
        self.assertFalse((self.target / ".agents/skills/bom").exists())
        record = json.loads((self.target / setup.SOURCE_RECORD).read_text(encoding="utf-8"))
        self.assertEqual(record["skipped_internal_skill_aliases"],
                         [{"path": ".agents/skills/bom", "target": "../../skills/bom"}])

    def test_skill_alias_escape_or_missing_real_target_is_rejected(self):
        for data in (self.happy_archive("../../../external"), self.happy_archive(include_target=False)):
            with patch.object(setup.shutil, "which", return_value=None), \
                 patch.object(setup, "_latest_commit", return_value=COMMIT), \
                 patch.object(setup, "_download_archive", side_effect=self.download(data)), \
                 self.assertRaisesRegex(RuntimeError, "Unsafe ZIP symbolic link"):
                setup.ensure_source(setup.HAPPY_URL, self.target)
            self.assertFalse(self.target.exists())

    def test_git_symlink_placeholder_is_omitted_without_touching_real_skill(self):
        def clone(git, url, checkout):
            self.source_files(checkout, setup.HAPPY_URL)
            alias = checkout / ".agents/skills/bom"
            alias.parent.mkdir(parents=True)
            alias.write_text("../../skills/bom", encoding="utf-8")
            return COMMIT
        with patch.object(setup.shutil, "which", return_value="git.exe"), \
             patch.object(setup, "_git_checkout", side_effect=clone):
            self.assertTrue(setup.ensure_source(setup.HAPPY_URL, self.target))
        self.assertFalse((self.target / ".agents/skills/bom").exists())
        self.assertTrue((self.target / "skills/bom/SKILL.md").is_file())

    def test_git_clone_uses_staging_and_records_actual_commit(self):
        def clone(git, url, checkout):
            self.assertEqual(git, "git.exe")
            self.assertNotEqual(checkout, self.target)
            self.source_files(checkout)
            return COMMIT
        with patch.object(setup.shutil, "which", return_value="git.exe"), \
             patch.object(setup, "_git_checkout", side_effect=clone), patch.object(setup, "_latest_commit") as api:
            self.assertTrue(setup.ensure_source(setup.MCP_URL, self.target))
        api.assert_not_called()
        record = json.loads((self.target / setup.SOURCE_RECORD).read_text(encoding="utf-8"))
        self.assertEqual(record["method"], "git_shallow_clone")
        self.assertIsNone(record["archive_sha256"])

    def test_concurrent_destination_content_is_preserved(self):
        def clone(git, url, checkout):
            self.source_files(checkout)
            self.target.mkdir()
            (self.target / "keep.txt").write_bytes(b"arrived meanwhile")
            return COMMIT
        with patch.object(setup.shutil, "which", return_value="git.exe"), \
             patch.object(setup, "_git_checkout", side_effect=clone), self.assertRaisesRegex(RuntimeError, "preserved"):
            setup.ensure_source(setup.MCP_URL, self.target)
        self.assertEqual((self.target / "keep.txt").read_bytes(), b"arrived meanwhile")

    def test_commit_metadata_validates_sha(self):
        url = "https://api.github.com/repos/mixelpixx/KiCAD-MCP-Server/commits?per_page=1"
        class Response(io.BytesIO):
            def geturl(self):
                return url
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(json.dumps([{"sha": COMMIT}]).encode())):
            self.assertEqual(setup._latest_commit("mixelpixx/KiCAD-MCP-Server"), COMMIT)
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(b'[{"sha":"main"}]')):
            with self.assertRaisesRegex(RuntimeError, "valid default-branch"):
                setup._latest_commit("mixelpixx/KiCAD-MCP-Server")

    def test_download_rejects_redirect_and_incomplete_response(self):
        repository = "mixelpixx/KiCAD-MCP-Server"
        url = f"https://codeload.github.com/{repository}/zip/{COMMIT}"
        class Response(io.BytesIO):
            headers = {"Content-Length": "100"}
            def geturl(self):
                return url
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(b"short")):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                setup._download_archive(repository, COMMIT, self.root / "partial.zip")
        redirected = Response(b"short")
        redirected.geturl = lambda: "https://example.invalid/archive.zip"
        with patch.object(setup.urllib.request, "urlopen", return_value=redirected):
            with self.assertRaisesRegex(RuntimeError, "redirected"):
                setup._download_archive(repository, COMMIT, self.root / "redirected.zip")

    def test_git_failure_preserves_empty_destination(self):
        self.target.mkdir()
        with patch.object(setup.shutil, "which", return_value="git.exe"), \
             patch.object(setup, "_git_checkout", side_effect=RuntimeError("network unavailable")), \
             self.assertRaisesRegex(RuntimeError, "staging evidence"):
            setup.ensure_source(setup.MCP_URL, self.target)
        self.assertEqual(list(self.target.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
