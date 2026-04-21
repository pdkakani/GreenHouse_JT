import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import state


class StateTests(unittest.TestCase):
    def test_record_job_preserves_alerted_flag(self):
        data = {"greenhouse:acme:123": {"alerted": True}}
        state.record_job(
            data,
            {
                "id": "123",
                "updated_at": "2026-04-20T00:00:00Z",
                "title": "Eng",
                "company": "Acme",
                "_ats": "greenhouse",
                "_source_slug": "acme",
            },
        )
        self.assertTrue(data["greenhouse:acme:123"]["alerted"])

    def test_load_state_migrates_legacy_greenhouse_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            state_file = tmp_path / "seen_jobs.json"
            legacy = {"123": {"updated_at": "x", "title": "y", "company": "acme", "alerted": True}}
            state_file.write_text(json.dumps(legacy), encoding="utf-8")

            with patch.object(state, "STATE_FILE", state_file):
                loaded = state.load_state()

        self.assertIn("greenhouse:acme:123", loaded)
        self.assertTrue(loaded["greenhouse:acme:123"]["alerted"])
        self.assertEqual(loaded["greenhouse:acme:123"]["ats"], "greenhouse")

    def test_enqueue_jobs_deduplicates(self):
        queue = [{"id": "1"}]
        state.enqueue_jobs(queue, [{"id": "1"}, {"id": "2", "title": "A"}])
        self.assertEqual([job["id"] for job in queue], ["1", "2"])

    def test_drop_expired_queue_entries(self):
        now = datetime.now(timezone.utc)
        queue = [
            {"id": "1", "queued_at": (now - timedelta(days=3)).isoformat()},
            {"id": "2", "queued_at": (now - timedelta(hours=1)).isoformat()},
            {"id": "3", "queued_at": "not-a-date"},
        ]
        with patch.object(state, "QUEUE_MAX_AGE_DAYS", 2):
            kept, dropped = state.drop_expired_queue_entries(queue)

        self.assertEqual(dropped, 1)
        self.assertEqual([item["id"] for item in kept], ["2", "3"])

    def test_save_and_load_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            state_file = tmp_path / "seen_jobs.json"
            with patch.object(state, "STATE_FILE", state_file):
                payload = {"greenhouse:z:1": {"updated_at": "x", "title": "y", "company": "z", "ats": "greenhouse", "source_slug": "z", "alerted": False}}
                state.save_state(payload)
                loaded = state.load_state()

        self.assertEqual(loaded, payload)


if __name__ == "__main__":
    unittest.main()
