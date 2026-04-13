from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_dispatch.setup_hooks import install_hooks


class SetupHooksTests(unittest.TestCase):
    def test_install_hooks_writes_portable_peer_hook_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            config_dir = home / ".config" / "ai-bridge"

            payload = install_hooks(home=home, config_dir=config_dir)

            self.assertTrue(payload["ok"])
            peers_hook = config_dir / "hooks" / "ai-peers-context.mjs"
            codex_hook = config_dir / "hooks" / "codex-orchestrator-context.mjs"
            opencode_plugin = home / ".config" / "opencode" / "plugins" / "ai-bridge-peers.ts"
            self.assertTrue(peers_hook.exists())
            self.assertTrue(codex_hook.exists())
            self.assertTrue(opencode_plugin.exists())
            self.assertIn("ai-peers", peers_hook.read_text(encoding="utf-8"))
            self.assertIn("chat.message", opencode_plugin.read_text(encoding="utf-8"))

            codex_hooks = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
            cursor_hooks = json.loads((home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))

            codex_session_commands = [
                hook["command"]
                for group in codex_hooks["hooks"]["SessionStart"]
                for hook in group["hooks"]
            ]
            self.assertTrue(any("codex-orchestrator-context.mjs" in item for item in codex_session_commands))
            self.assertTrue(any("ai-peers-context.mjs" in item for item in codex_session_commands))

            cursor_commands = [
                item["command"]
                for item in cursor_hooks["hooks"]["UserPromptSubmit"]
            ]
            self.assertTrue(any("ai-peers-context.mjs" in item for item in cursor_commands))

    def test_install_hooks_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            config_dir = home / ".config" / "ai-bridge"

            install_hooks(home=home, config_dir=config_dir)
            install_hooks(home=home, config_dir=config_dir)

            codex_hooks = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
            cursor_hooks = json.loads((home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))

            codex_commands = [
                hook["command"]
                for groups in codex_hooks["hooks"].values()
                for group in groups
                for hook in group.get("hooks", [])
            ]
            cursor_commands = [
                item["command"]
                for groups in cursor_hooks["hooks"].values()
                for item in groups
            ]
            self.assertEqual(
                len([item for item in codex_commands if "ai-peers-context.mjs" in item]),
                2,
            )
            self.assertEqual(
                len([item for item in cursor_commands if "ai-peers-context.mjs" in item]),
                2,
            )

    def test_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            config_dir = home / ".config" / "ai-bridge"

            payload = install_hooks(home=home, config_dir=config_dir, dry_run=True)

            self.assertTrue(payload["dry_run"])
            self.assertFalse((home / ".codex" / "hooks.json").exists())
            self.assertFalse((config_dir / "hooks" / "ai-peers-context.mjs").exists())


if __name__ == "__main__":
    unittest.main()
