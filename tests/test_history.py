"""Synthetic fixtures only; never read the developer's real Codex home."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "codex_history.py"
spec = importlib.util.spec_from_file_location("history", SCRIPT)
h = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = h
spec.loader.exec_module(h)
SID = "00000000-0000-4000-8000-000000000001"


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "codex"
        (self.home / "sessions").mkdir(parents=True)
        self.output = self.root / "vault" / "来源" / "会话"
        self.source = self.home / "sessions" / (SID + ".jsonl")

    def write(self, records):
        self.source.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    def event(self, text="缓存 使用本地索引"):
        return {"type": "event_msg", "payload": {"type": "user_message", "message": text}}

    def sync(self, **kwargs):
        return h.sync_history(self.home, self.output, quiet=True, **kwargs)

    def test_fallback_channels_and_context(self):
        records = []
        for role, channel, value in [("assistant", "analysis", "SECRET_REASONING"), ("assistant", None, "UNKNOWN_CHANNEL"), ("assistant", "final", "VISIBLE"), ("system", None, "SYSTEM_SECRET")]:
            records.append({"type": "response_item", "payload": {"type": "message", "role": role, "channel": channel, "content": [{"type": "output_text", "text": value}]}})
        records.append({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>HIDDEN_ENV</environment_context>QUESTION"}]}})
        self.write(records)
        text = h.render_markdown(h.parse_session(self.source, "active", {}))
        for secret in ("SECRET_REASONING", "UNKNOWN_CHANNEL", "SYSTEM_SECRET", "HIDDEN_ENV"):
            self.assertNotIn(secret, text)
        self.assertIn("VISIBLE", text)
        self.assertIn("QUESTION", text)

    def test_event_analysis_filtered(self):
        self.write([self.event(), {"type": "event_msg", "payload": {"type": "agent_message", "channel": "analysis", "message": "SECRET_REASONING"}}])
        self.assertNotIn("SECRET_REASONING", h.render_markdown(h.parse_session(self.source, "active", {})))

    def test_redaction(self):
        values = ["person@example.invalid", "13800000000", "ghp_" + "x" * 30, "sk-" + "x" * 30, "password=synthetic-password", "Bearer synthetic-token", "C:\\Users\\ExamplePerson\\file", "/home/ExamplePerson/file", "-----BEGIN PRIVATE KEY-----\nsynthetic-key\n-----END PRIVATE KEY-----"]
        result = h.clean_message("\n".join(values))
        for value in ("person@example.invalid", "13800000000", "x" * 30, "synthetic-password", "synthetic-token", "ExamplePerson", "synthetic-key"):
            self.assertNotIn(value, result)

    def test_sync_search_and_prune(self):
        self.write([self.event()])
        before = self.source.read_bytes()
        self.sync()
        self.assertEqual(before, self.source.read_bytes())
        results = h.search_history(self.home, self.output, "缓存", limit=8, auto_sync=False)
        self.assertTrue(results)
        mirror = Path(h.load_manifest(self.output)["items"][SID]["output_path"])
        self.source.unlink()
        self.assertEqual(self.sync()["已清理来源"], 1)
        self.assertFalse(mirror.exists())
        self.assertFalse(h.search_history(self.home, self.output, "缓存", limit=8, auto_sync=False))

    def test_partial_sync_never_prunes(self):
        self.write([self.event()])
        self.sync()
        self.source.unlink()
        self.sync(limit=1)
        self.assertIn(SID, h.load_manifest(self.output)["items"])

    def test_missing_source_directory_fails_closed(self):
        self.write([self.event()])
        self.sync()
        self.source.unlink()
        self.source.parent.rmdir()
        with self.assertRaises(ValueError):
            self.sync()
        self.assertIn(SID, h.load_manifest(self.output)["items"])

    def test_manually_owned_notes_survive(self):
        self.write([self.event()])
        self.sync()
        note = self.root / "vault" / "项目" / "_自动索引" / "manual.md"
        note.write_text("manual note", encoding="utf-8")
        mirror = Path(h.load_manifest(self.output)["items"][SID]["output_path"])
        mirror.write_text("manual replacement", encoding="utf-8")
        self.source.unlink()
        self.sync()
        self.assertTrue(note.exists())
        self.assertEqual(mirror.read_text(encoding="utf-8"), "manual replacement")

    def test_manifest_cannot_delete_external_file(self):
        self.write([self.event()])
        self.sync()
        external = self.root / (SID + ".md")
        external.write_text("keep", encoding="utf-8")
        manifest = h.load_manifest(self.output)
        manifest["items"][SID]["output_path"] = str(external)
        h.save_manifest(self.output, manifest)
        self.source.unlink()
        self.sync()
        self.assertEqual(external.read_text(), "keep")

    def test_privacy_upgrade_rewrites_unchanged_source(self):
        self.write([self.event("person@example.invalid")])
        self.sync()
        manifest = h.load_manifest(self.output)
        manifest.pop("privacy_version")
        mirror = Path(manifest["items"][SID]["output_path"])
        mirror.write_text("person@example.invalid", encoding="utf-8")
        h.save_manifest(self.output, manifest)
        self.sync()
        self.assertNotIn("person@example.invalid", mirror.read_text(encoding="utf-8"))

    def test_overlapping_directory_rejected(self):
        with self.assertRaises(ValueError):
            h.sync_history(self.home, self.home / "vault" / "来源" / "会话", quiet=True)

    def test_cli_custom_paths(self):
        self.write([self.event()])
        env = dict(os.environ, PYTHONUTF8="1")
        result = subprocess.run([sys.executable, str(SCRIPT), "--codex-home", str(self.home), "--output", str(self.output), "sync", "--json"], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["失败"], 0)

    def test_partial_upgrade_does_not_mark_all_mirrors_current(self):
        self.write([self.event()])
        self.sync()
        manifest = h.load_manifest(self.output)
        manifest.pop("privacy_version")
        manifest["items"][SID].pop("privacy_version")
        mirror = Path(manifest["items"][SID]["output_path"])
        mirror.write_text("OLD_PRIVATE_TEXT", encoding="utf-8")
        h.save_manifest(self.output, manifest)
        self.sync(limit=0)
        self.sync()
        self.assertNotIn("OLD_PRIVATE_TEXT", mirror.read_text(encoding="utf-8"))

    def test_date_change_removes_old_owned_mirror(self):
        def records(date):
            return [{"type": "session_meta", "payload": {"id": SID, "timestamp": date}}, self.event()]
        self.write(records("2025-01-01"))
        self.sync()
        old = Path(h.load_manifest(self.output)["items"][SID]["output_path"])
        self.write(records("2025-02-01"))
        self.sync(force=True)
        new = Path(h.load_manifest(self.output)["items"][SID]["output_path"])
        self.assertNotEqual(old, new)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_tool_recipient_is_not_exported(self):
        self.write([{"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "commentary", "recipient": "tools.example", "content": [{"type": "output_text", "text": "TOOL_COMMAND"}]}}])
        self.assertEqual(h.parse_session(self.source, "active", {}).messages, [])

    def test_malformed_non_object_records_are_ignored(self):
        self.write([[], None, self.event()])
        self.assertEqual(self.sync()["失败"], 0)

    def test_knowledge_is_ranked_above_sources(self):
        self.write([self.event()])
        self.sync()
        knowledge = self.root / "vault" / "知识" / "决策"
        knowledge.mkdir(parents=True)
        (knowledge / "cache.md").write_text("# 缓存\n使用本地索引。", encoding="utf-8")
        results = h.search_history(self.home, self.output, "缓存", limit=8, auto_sync=False)
        self.assertEqual(results[0]["层级"], "知识")


if __name__ == "__main__":
    unittest.main()
