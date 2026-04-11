from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AI_BRIDGE_CONFIG_DIR"] = os.path.join(self.tempdir.name, "config")
        Path(os.environ["AI_BRIDGE_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

        import ai_dispatch.jobs as jobs_module
        import ai_dispatch.adapters as adapters_module
        import ai_dispatch.routing as routing_module

        self.jobs = importlib.reload(jobs_module)
        self.adapters = importlib.reload(adapters_module)
        self.routing = importlib.reload(routing_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_BRIDGE_CONFIG_DIR", None)

    def test_normalize_target_aliases(self) -> None:
        self.assertEqual(self.adapters.normalize_target("cursor-agent"), "cursor")
        self.assertEqual(self.adapters.normalize_target("claude-code"), "claude")
        self.assertEqual(self.adapters.normalize_target("qwen-code"), "qwen")
        self.assertEqual(self.adapters.normalize_target("qwen-cli"), "qwen")
        self.assertEqual(self.adapters.normalize_target("codex"), "codex")
        self.assertEqual(self.adapters.normalize_target("my-custom-agent"), "my-custom-agent")

    def test_qwen_builtin_command_uses_cli_non_interactive_shape(self) -> None:
        command = self.adapters.builtin_worker_command("qwen", "Investigate", "/tmp/repo")
        self.assertEqual(
            command,
            [
                "qwen",
                "--include-directories",
                "/tmp/repo",
                "--approval-mode",
                "yolo",
                "--output-format",
                "text",
                "Investigate",
            ],
        )

    def test_render_adapter_command(self) -> None:
        rendered = self.adapters.render_adapter_command(
            ["tool", "--cwd", "{cwd}", "--message", "{prompt}", "--from", "{source}"],
            prompt="Investigate",
            cwd="/tmp/repo",
            source="codex",
            target="gemini",
            difficulty="hard",
        )
        self.assertEqual(rendered, ["tool", "--cwd", "/tmp/repo", "--message", "Investigate", "--from", "codex"])

    def test_classify_task_debugging_hard(self) -> None:
        payload = self.routing.classify_task("Debug the race condition in the sync engine", requested_difficulty="auto")
        self.assertEqual(payload["category"], "debugging")
        self.assertEqual(payload["complexity"], "hard")

    def test_classify_task_category_coverage(self) -> None:
        self.assertEqual(
            self.routing.classify_task("Add support for CSV export", requested_difficulty="auto")["category"],
            "implementation",
        )
        self.assertEqual(
            self.routing.classify_task("Refactor the auth module into smaller files", requested_difficulty="auto")[
                "category"
            ],
            "refactor",
        )
        self.assertEqual(
            self.routing.classify_task("Summarize differences between two approaches", requested_difficulty="auto")[
                "category"
            ],
            "research",
        )
        self.assertEqual(
            self.routing.classify_task("Review this PR for regressions", requested_difficulty="auto")["category"],
            "review",
        )
        self.assertEqual(
            self.routing.classify_task("Rename docs heading", requested_difficulty="auto")["category"],
            "simple_edit",
        )

    def test_auto_route_defaults_to_primary_four(self) -> None:
        payload = self.routing.route_task(
            task="Rename the settings toggle label",
            target="auto",
            requested_difficulty="auto",
            cwd=self.tempdir.name,
        )
        self.assertEqual(payload["route"][0], "opencode")
        self.assertTrue(set(item["agent"] for item in payload["scores"]).issubset({"codex", "claude", "cursor", "opencode"}))

    def test_explicit_target_route_contains_only_requested_worker(self) -> None:
        payload = self.routing.route_task(
            task="Investigate flaky test",
            target="goose",
            requested_difficulty="auto",
            cwd=self.tempdir.name,
        )
        self.assertEqual(payload["route"], ["goose"])
        self.assertEqual(payload["scores"], [])

    def test_optional_allowlist_requires_scores(self) -> None:
        config_path = Path(os.environ["AI_BRIDGE_CONFIG_DIR"]) / "routing.json"
        config_path.write_text(
            json.dumps(
                {
                    "auto_routing": {
                        "enabled_agents": ["codex", "claude", "cursor", "opencode"],
                        "optional_allowlist": ["goose"],
                    }
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "goose"):
            self.routing.route_task(
                task="Rename the settings toggle label",
                target="auto",
                requested_difficulty="auto",
                cwd=self.tempdir.name,
            )

    def test_optional_allowlist_can_join_auto_route(self) -> None:
        config_path = Path(os.environ["AI_BRIDGE_CONFIG_DIR"]) / "routing.json"
        config_path.write_text(
            json.dumps(
                {
                    "auto_routing": {
                        "enabled_agents": ["codex", "claude", "cursor", "opencode"],
                        "optional_allowlist": ["goose"],
                    },
                    "agents": {
                        "goose": {
                            "scores": {
                                "simple_edit": 20,
                                "implementation": 6,
                                "debugging": 6,
                                "refactor": 5,
                                "research": 5,
                                "review": 4,
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        payload = self.routing.route_task(
            task="Rename the settings toggle label",
            target="auto",
            requested_difficulty="auto",
            cwd=self.tempdir.name,
        )
        self.assertEqual(payload["route"][0], "goose")

    def test_requested_difficulty_overrides_classifier_complexity(self) -> None:
        payload = self.routing.classify_task(
            "Debug the race condition in the sync engine",
            requested_difficulty="easy",
        )
        self.assertEqual(payload["category"], "debugging")
        self.assertEqual(payload["complexity"], "easy")
        self.assertTrue(any("override:difficulty:easy" in signal for signal in payload["signals"]))

    def test_auto_route_research_uses_single_agent(self) -> None:
        payload = self.routing.route_task(
            task="Research the tradeoffs between sqlite and postgres",
            target="auto",
            requested_difficulty="auto",
            cwd=self.tempdir.name,
        )
        self.assertEqual(len(payload["route"]), 1)
        self.assertEqual(payload["classifier"]["category"], "research")

    def test_explicit_target_short_circuits_auto_pool(self) -> None:
        payload = self.routing.route_task(
            task="Anything",
            target="CLAUDE-CODE",
            requested_difficulty="auto",
            cwd=self.tempdir.name,
        )
        self.assertEqual(payload["route"], ["claude"])
        self.assertIn("Explicit", payload["route_reason"])
