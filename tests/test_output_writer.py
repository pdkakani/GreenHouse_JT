import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import output_writer


class OutputWriterTests(unittest.TestCase):
    def test_write_jobs_markdown_groups_by_ats(self):
        jobs = [
            {
                "id": "1",
                "title": "Backend Engineer",
                "company": "stripe",
                "_ats": "greenhouse",
                "_location": "Remote, USA",
                "_department": "Engineering",
                "_url": "https://example.com/1",
                "_score": 91,
                "updated_at": "2026-04-21T00:00:00Z",
            },
            {
                "id": "3",
                "title": "Data Platform Engineer",
                "company": "stripe",
                "_ats": "greenhouse",
                "_status": "updated",
                "_location": "Remote, USA",
                "_department": "Engineering",
                "_url": "https://example.com/3",
                "updated_at": "2026-04-21T00:02:00Z",
            },
            {
                "id": "2",
                "title": "Platform Engineer",
                "company": "kraken",
                "_ats": "lever",
                "_location": "Remote, USA",
                "_department": "Engineering",
                "_url": "https://example.com/2",
                "_score": 83,
                "updated_at": "2026-04-21T00:01:00Z",
            },
        ]
        stats = {
            "jobs_new": 2,
            "jobs_fetched": 2,
            "jobs_updated": 0,
            "by_ats": {
                "greenhouse": {"jobs_new": 1, "jobs_fetched": 1, "jobs_updated": 0, "jobs_alerted": 1},
                "lever": {"jobs_new": 1, "jobs_fetched": 1, "jobs_updated": 0, "jobs_alerted": 1},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with patch.object(output_writer, "OUTPUT_FILE", tmp_path / "jobs.md"):
                wrote = output_writer.write_jobs_markdown(jobs, stats, "2026-04-21 00:05 UTC")

            content = (tmp_path / "jobs.md").read_text(encoding="utf-8")

        self.assertTrue(wrote)
        self.assertIn("## 📅 Run: 2026-04-21 00:05 UTC", content)
        self.assertIn("### Greenhouse", content)
        self.assertIn("### Lever", content)
        self.assertIn("🔄 Data Platform Engineer", content)
        self.assertIn("ATS Summary", content)


if __name__ == "__main__":
    unittest.main()
