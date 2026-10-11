"""Offline MCP registration regressions; all writes use tempfile directories.

Run beside the other setup scripts with Python 3.11 or newer. TEMP/TMP may be
set by the caller to keep the temporary fixtures under an audit directory.
No KiCad, Node.js or server processes are launched by these tests.
"""
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from environment_setup import register_mcp


class AliasRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-register-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.toml"
        # Only launch-path strings are needed, not installed programs. Remove
        # the temp directory's ancestors so "kicad" in a user's workspace path
        # cannot accidentally make the former substring-based code pass.
        self.install = Path(self.root.anchor) / "EDA tools" / self.root.name / "service"
        self.launch = {
            "command": "node.exe",
            "args": [str(self.install / "dist/index.js"), "--config", str(self.root / "settings/config.json")],
            "cwd": str(self.install),
            "env": {"KICAD_BACKEND": "swig"},
        }

    def section(self, name, entry, cwd=None, extras=""):
        text = f'[mcp_servers.{json.dumps(name)}]\ncommand = "node.exe"\nargs = [{json.dumps(entry)}]\n'
        if cwd is not None:
            text += "cwd = " + json.dumps(str(cwd)) + "\n"
        return text + extras

    def configure(self, text):
        self.config_path.write_text(text, encoding="utf-8")
        result = register_mcp(self.root, self.config_path, tomllib.loads(text), self.launch)
        return result, tomllib.loads(self.config_path.read_text(encoding="utf-8"))

    def test_arbitrary_alias_and_absolute_custom_path_reused(self):
        text = self.section("pcb-service", str(self.install / "dist/index.js"), self.install,
                            'disabled_tools = ["unchanged-tool"]\n[mcp_servers."pcb-service".env]\nMY_OPTION = "preserve"\n')
        result, after = self.configure(text)
        self.assertEqual(result["name"], "pcb-service")
        self.assertEqual(set(after["mcp_servers"]), {"pcb-service"})
        self.assertEqual(after["mcp_servers"]["pcb-service"]["disabled_tools"], ["unchanged-tool"])
        self.assertEqual(after["mcp_servers"]["pcb-service"]["env"]["MY_OPTION"], "preserve")
        second = register_mcp(self.root, self.config_path, after, self.launch)
        self.assertFalse(second["changed"])

    def test_relative_entry_with_cwd_resolves_same_installation(self):
        result, after = self.configure(self.section("user-alias", "dist/../dist/index.js", self.install))
        self.assertEqual(result["name"], "user-alias")
        self.assertEqual(set(after["mcp_servers"]), {"user-alias"})

    def test_unrelated_path_containing_kicad_is_not_hijacked(self):
        decoy = self.root / "kicad-unrelated/dist/index.js"
        text = self.section("other", str(decoy), decoy.parent.parent, 'disabled_tools = ["keep"]\n')
        result, after = self.configure(text)
        self.assertEqual(result["name"], "kicad")
        self.assertEqual(after["mcp_servers"]["other"], tomllib.loads(text)["mcp_servers"]["other"])

    def test_duplicate_matching_aliases_are_rejected_without_writing(self):
        text = self.section("pcb-one", "dist/index.js", self.install) + self.section("pcb-two", str(self.install / "dist/index.js"))
        self.config_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Multiple MCP"):
            register_mcp(self.root, self.config_path, tomllib.loads(text), self.launch)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), text)

    def test_unrelated_reserved_name_cannot_be_replaced(self):
        text = self.section("kicad", str(self.root / "different/dist/index.js"))
        self.config_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unrelated MCP server"):
            register_mcp(self.root, self.config_path, tomllib.loads(text), self.launch)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), text)


class ConfigPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-preserve-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.toml"
        install = self.root / "tools/KiCAD-MCP-Server"
        self.launch = {
            "command": str(self.root / "Program Files/nodejs/node.exe"),
            "args": [str(install / "dist/index.js")],
            "cwd": str(install),
            "env": {"KICAD_CLI": str(self.root / "KiCad/bin/kicad-cli.exe")},
        }

    def configure(self, text):
        self.config_path.write_text(text, encoding="utf-8")
        result = register_mcp(self.root, self.config_path, tomllib.loads(text), self.launch)
        return result, tomllib.loads(self.config_path.read_text(encoding="utf-8"))

    def test_new_registration_and_repeat_no_write(self):
        result, after = self.configure('model = "unchanged"\n[mcp_servers.other]\ncommand = "preserve-me"\n')
        self.assertTrue(result["changed"])
        self.assertEqual(after["model"], "unchanged")
        self.assertEqual(after["mcp_servers"]["other"], {"command": "preserve-me"})
        before_bytes = self.config_path.read_bytes()
        second = register_mcp(self.root, self.config_path, after, self.launch)
        self.assertFalse(second["changed"])
        self.assertEqual(self.config_path.read_bytes(), before_bytes)

    def test_existing_subtables_and_policy_are_preserved(self):
        text = f'''model = "unchanged"
[mcp_servers.kicad]
command = "old-node"
args = [{json.dumps(self.launch["args"][0])}]
disabled_tools = ["dangerous-tool"]
[mcp_servers.kicad.env]
EXTRA_OPTION = "preserve-me"
[mcp_servers.kicad.tools.read]
approval_mode = "prompt"
[desktop]
theme = "keep"
[mcp_servers.other]
command = "other"
'''
        result, after = self.configure(text)
        self.assertTrue(result["changed"])
        kicad = after["mcp_servers"]["kicad"]
        self.assertEqual(kicad["env"]["EXTRA_OPTION"], "preserve-me")
        self.assertEqual(kicad["disabled_tools"], ["dangerous-tool"])
        self.assertEqual(kicad["tools"]["read"]["approval_mode"], "prompt")
        self.assertEqual(after["desktop"], {"theme": "keep"})

    def test_unrelated_name_collision_is_rejected(self):
        text = '[mcp_servers.kicad]\ncommand = "unrelated-server"\n'
        self.config_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unrelated MCP server"):
            register_mcp(self.root, self.config_path, tomllib.loads(text), self.launch)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), text)

    def test_non_ascii_paths_are_round_tripped(self):
        self.launch["cwd"] = str(self.root / (chr(0x4E2D) + chr(0x6587)) / "[project]")
        _, after = self.configure("# original comment\nmodel = 'keep'\n")
        self.assertEqual(after["mcp_servers"]["kicad"]["cwd"], self.launch["cwd"])
        self.assertIn("# original comment", self.config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
