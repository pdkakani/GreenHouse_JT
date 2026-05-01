import os
import unittest
from unittest.mock import patch

import ats_sources


class ATSSourcesTests(unittest.TestCase):
    def test_ats_enabled_respects_env(self):
        with patch.dict(os.environ, {"ENABLED_ATS": "lever,ashby"}):
            self.assertEqual(ats_sources.ats_enabled(), ["lever", "ashby"])

    def test_normalize_greenhouse_job(self):
        source = ats_sources.ATSSource("greenhouse", "acme")
        raw = {
            "id": 123,
            "title": "Backend Engineer",
            "location": {"name": "Remote, USA"},
            "departments": [{"name": "Engineering"}],
            "absolute_url": "https://example.com/job/123",
            "content": "<p>Build APIs</p>",
            "updated_at": "2026-04-21T00:00:00Z",
        }

        job = ats_sources.normalize_job(source, raw)

        self.assertEqual(job["id"], "123")
        self.assertEqual(job["_ats"], "greenhouse")
        self.assertEqual(job["_source_slug"], "acme")
        self.assertEqual(job["_location"], "Remote, USA")
        self.assertEqual(job["_department"], "Engineering")
        self.assertEqual(job["_url"], "https://example.com/job/123")

    def test_normalize_lever_job(self):
        source = ats_sources.ATSSource("lever", "kraken")
        raw = {
            "id": "abc",
            "text": "Platform Engineer",
            "categories": {"location": "Remote, USA", "team": "Engineering"},
            "hostedUrl": "https://jobs.lever.co/kraken/abc",
            "descriptionPlain": "Build systems",
            "updatedAt": "2026-04-21T00:00:00Z",
        }

        job = ats_sources.normalize_job(source, raw)

        self.assertEqual(job["id"], "abc")
        self.assertEqual(job["_ats"], "lever")
        self.assertEqual(job["_location"], "Remote, USA")
        self.assertEqual(job["_department"], "Engineering")
        self.assertEqual(job["_url"], "https://jobs.lever.co/kraken/abc")

    def test_normalize_ashby_job_requires_listed_jobs(self):
        source = ats_sources.ATSSource("ashby", "ashby")
        raw = {
            "id": "job-1",
            "title": "Staff Engineer",
            "location": "Remote, USA",
            "department": "Engineering",
            "jobUrl": "https://jobs.ashbyhq.com/ashby/job-1",
            "descriptionPlain": "Build product",
            "publishedAt": "2026-04-21T00:00:00Z",
            "isListed": True,
        }

        job = ats_sources.normalize_job(source, raw)

        self.assertEqual(job["id"], "job-1")
        self.assertEqual(job["_ats"], "ashby")
        self.assertEqual(job["_location"], "Remote, USA")
        self.assertEqual(job["_department"], "Engineering")
        self.assertEqual(job["_url"], "https://jobs.ashbyhq.com/ashby/job-1")

        self.assertIsNone(
            ats_sources.normalize_job(
                source,
                {
                    **raw,
                    "isListed": False,
                },
            )
        )

    def test_normalize_smartrecruiters_job(self):
        source = ats_sources.ATSSource("smartrecruiters", "acme")
        raw = {
            "id": "90001",
            "name": "Senior Software Engineer",
            "location": {"city": "Remote", "region": "USA"},
            "department": {"label": "Engineering"},
            "ref": "https://jobs.smartrecruiters.com/acme/90001",
            "releasedDate": "2026-04-21T00:00:00Z",
            "jobAd": {
                "sections": {
                    "jobDescription": {
                        "text": "Build distributed systems",
                    }
                }
            },
        }

        job = ats_sources.normalize_job(source, raw)

        self.assertEqual(job["id"], "90001")
        self.assertEqual(job["_ats"], "smartrecruiters")
        self.assertEqual(job["_location"], "Remote, USA")
        self.assertEqual(job["_department"], "Engineering")
        self.assertEqual(job["_url"], "https://jobs.smartrecruiters.com/acme/90001")

    def test_normalize_smartrecruiters_job_uuid_and_section_list(self):
        source = ats_sources.ATSSource("smartrecruiters", "acme")
        raw = {
            "uuid": "uuid-1",
            "name": "Platform Engineer",
            "location": {"city": "Austin", "country": "USA"},
            "function": {"label": "Engineering"},
            "applyUrl": "https://careers.smartrecruiters.com/acme/platform-engineer",
            "jobAd": {
                "sections": [
                    {"text": "Build APIs"},
                    {"text": "Improve reliability"},
                ]
            },
            "postingDate": "2026-04-21T00:00:00Z",
        }

        job = ats_sources.normalize_job(source, raw)

        self.assertEqual(job["id"], "uuid-1")
        self.assertEqual(job["_department"], "Engineering")
        self.assertEqual(job["content"], "Build APIs\n\nImprove reliability")


if __name__ == "__main__":
    unittest.main()
