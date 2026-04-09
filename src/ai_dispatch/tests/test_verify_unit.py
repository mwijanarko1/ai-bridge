from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class VerifyConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tempdir.name) / "config"
        self.config_dir.mkdir(parents=True)
        os.environ["AI_BRIDGE_CONFIG_DIR"] = str(self.config_dir)

        import importlib

        import ai_dispatch.jobs as jobs_module
        import ai_dispatch.verify as verify_module

        importlib.reload(jobs_module)
        self.verify = importlib.reload(verify_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_BRIDGE_CONFIG_DIR", None)

    def test_load_invalid_json_raises(self) -> None:
        (self.config_dir / "verify.json").write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid verify config"):
            self.verify.load_verify_config(self.tempdir.name)

    def test_prepare_missing_profile_raises(self) -> None:
        (self.config_dir / "verify.json").write_text(
            json.dumps({"profiles": {"default": {"command": [sys.executable, "-c", "pass"]}}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "not found"):
            self.verify.prepare_verification("full", cwd=self.tempdir.name, override_path=None)

    def test_prepare_missing_profiles_object_raises(self) -> None:
        (self.config_dir / "verify.json").write_text(json.dumps({"profiles": "nope"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no valid profiles"):
            self.verify.prepare_verification("default", cwd=self.tempdir.name, override_path=None)


if __name__ == "__main__":
    unittest.main()
