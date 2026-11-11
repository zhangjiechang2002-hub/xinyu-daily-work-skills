import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from project_profile import load_profile, validate_active_quality, validate_profile


class PublicPackageTests(unittest.TestCase):
    def test_example_is_intentionally_not_runnable(self):
        example = json.loads((ROOT / "profiles" / "new-project.example.json").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            validate_profile(example)

    def test_unbound_project_has_no_profile(self):
        self.assertIsNone(load_profile({"game_prefix": "UNKNOWN"}))

    def test_independent_profile_can_be_validated(self):
        profile = json.loads((ROOT / "profiles" / "new-project.example.json").read_text(encoding="utf-8"))
        profile["project_key"] = "DEMO"
        profile["aliases"] = ["Demo Game"]
        profile["shared_workspace"] = "C:/private/feishu-adapter"
        profile["destination"] = {"wiki_token": "private-token", "template_sheet_id": "template-id"}
        profile["quality"]["GP"]["roi_green"]["首日ROI"] = 0.1
        validate_profile(profile)
        validate_active_quality(profile, "GP")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.json"
            path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
            loaded = load_profile({"game_prefix": "Demo Game", "project_profile": str(path)})
            self.assertEqual(loaded["project_key"], "DEMO")

    def test_public_runner_contains_no_private_destinations(self):
        text = (ROOT / "scripts" / "run_weekly_game_report.py").read_text(encoding="utf-8")
        self.assertIn('AILIN2_WIKI_TOKEN = ""', text)
        self.assertIn('SHENYU_TURKEY_WIKI_TOKEN = ""', text)


if __name__ == "__main__":
    unittest.main()

