import unittest
from unittest.mock import Mock, patch

from ats_sources import ATSSource
import poller


def make_job(job_id, title, location, department, updated_at, company="Acme", content="<>"):
    return {
        "id": job_id,
        "title": title,
        "company": company,
        "location": {"name": location},
        "departments": [{"name": department}],
        "updated_at": updated_at,
        "absolute_url": f"https://example.com/jobs/{job_id}",
        "content": content,
    }


class PollerIntegrationTests(unittest.TestCase):
    def test_main_scores_new_job_and_skips_unchanged_job(self):
        state = {
            "greenhouse:acme:1": {
                "updated_at": "2026-04-20T00:00:00Z",
                "title": "Backend Engineer",
                "company": "Acme",
                "ats": "greenhouse",
                "source_slug": "acme",
                "alerted": False,
            }
        }
        jobs = [
            make_job("1", "Backend Engineer", "Remote, USA", "Engineering", "2026-04-21T00:00:00Z"),
            make_job("2", "Platform Engineer", "Remote, USA", "Engineering", "2026-04-21T00:00:00Z"),
        ]

        score_job = Mock(return_value=88)
        send_slack_alert = Mock(return_value=True)
        send_digest = Mock(return_value=True)

        with (
            patch.object(poller, "load_sources", return_value=[ATSSource("greenhouse", "acme")]),
            patch.object(poller, "fetch_jobs", return_value=jobs),
            patch.object(poller, "load_state", return_value=state),
            patch.object(poller, "save_state") as save_state,
            patch.object(poller, "score_job", score_job),
            patch.object(poller, "send_slack_alert", send_slack_alert),
            patch.object(poller, "send_new_jobs_digest", send_digest),
            patch.object(poller, "write_jobs_markdown"),
            patch.object(poller, "sleep_between_scores"),
            patch.object(poller.sys, "exit") as sys_exit,
        ):
            poller.main()

        score_job.assert_called_once()
        send_slack_alert.assert_called_once()
        send_digest.assert_called_once()
        sys_exit.assert_called_once_with(0)

        self.assertEqual(score_job.call_args.args[0]["id"], "2")
        self.assertEqual(send_slack_alert.call_args.args[0]["_score"], 88)
        self.assertEqual(len(send_digest.call_args.args[0]), 1)
        self.assertEqual(send_digest.call_args.args[0][0]["id"], "2")
        self.assertEqual(send_digest.call_args.args[0][0]["_score"], 88)
        self.assertEqual(state["greenhouse:acme:1"]["updated_at"], "2026-04-21T00:00:00Z")
        self.assertTrue(state["greenhouse:acme:2"]["alerted"])
        self.assertEqual(save_state.call_count, 2)

    def test_main_filters_non_technical_job_before_scoring(self):
        state = {}
        jobs = [
            make_job("3", "HR Manager", "Remote, USA", "People", "2026-04-21T00:00:00Z"),
        ]

        score_job = Mock()
        send_slack_alert = Mock()
        send_digest = Mock(return_value=True)

        with (
            patch.object(poller, "load_sources", return_value=[ATSSource("greenhouse", "acme")]),
            patch.object(poller, "fetch_jobs", return_value=jobs),
            patch.object(poller, "load_state", return_value=state),
            patch.object(poller, "save_state") as save_state,
            patch.object(poller, "score_job", score_job),
            patch.object(poller, "send_slack_alert", send_slack_alert),
            patch.object(poller, "send_new_jobs_digest", send_digest),
            patch.object(poller, "write_jobs_markdown"),
            patch.object(poller, "sleep_between_scores"),
            patch.object(poller.sys, "exit") as sys_exit,
        ):
            poller.main()

        score_job.assert_not_called()
        send_slack_alert.assert_not_called()
        send_digest.assert_not_called()
        sys_exit.assert_called_once_with(0)
        self.assertEqual(state, {})
        self.assertEqual(save_state.call_count, 1)

    def test_main_groups_sources_by_ats(self):
        state = {}
        greenhouse_jobs = [
            make_job("g1", "Backend Engineer", "Remote, USA", "Engineering", "2026-04-21T00:00:00Z", company="stripe"),
        ]
        lever_jobs = [
            {
                "id": "l1",
                "text": "Platform Engineer",
                "categories": {"location": "Remote, USA", "team": "Engineering"},
                "hostedUrl": "https://jobs.lever.co/stripe/l1",
                "updatedAt": "2026-04-21T00:01:00Z",
                "descriptionPlain": "Build things",
            }
        ]

        score_job = Mock(side_effect=[91, 83])
        send_slack_alert = Mock(return_value=True)
        send_digest = Mock(return_value=True)

        with (
            patch.object(poller, "load_sources", return_value=[ATSSource("greenhouse", "stripe"), ATSSource("lever", "stripe")]),
            patch.object(poller, "fetch_jobs", side_effect=[greenhouse_jobs, lever_jobs]),
            patch.object(poller, "load_state", return_value=state),
            patch.object(poller, "save_state"),
            patch.object(poller, "score_job", score_job),
            patch.object(poller, "send_slack_alert", send_slack_alert),
            patch.object(poller, "send_new_jobs_digest", send_digest),
            patch.object(poller, "write_jobs_markdown"),
            patch.object(poller, "sleep_between_scores"),
            patch.object(poller.sys, "exit") as sys_exit,
        ):
            poller.main()

        self.assertEqual(score_job.call_count, 2)
        self.assertEqual(send_slack_alert.call_count, 2)
        digest_jobs = send_digest.call_args.args[0]
        self.assertEqual({job["_ats"] for job in digest_jobs}, {"greenhouse", "lever"})
        self.assertTrue(any(job["_ats"] == "lever" for job in digest_jobs))
        sys_exit.assert_called_once_with(0)

    def test_main_caps_scoring_to_top_three_per_ats(self):
        state = {}
        greenhouse_jobs = [
            make_job(f"g{i}", f"Greenhouse {i}", "Remote, USA", "Engineering", f"2026-04-21T00:0{i}:00Z", company="stripe")
            for i in range(4)
        ]
        lever_jobs = [
            {
                "id": f"l{i}",
                "text": f"Lever {i}",
                "categories": {"location": "Remote, USA", "team": "Engineering"},
                "hostedUrl": f"https://jobs.lever.co/stripe/l{i}",
                "updatedAt": f"2026-04-21T00:1{i}:00Z",
                "descriptionPlain": "Build things",
            }
            for i in range(4)
        ]

        score_job = Mock(side_effect=[90, 89, 88, 87, 86, 85])
        send_slack_alert = Mock(return_value=True)
        send_digest = Mock(return_value=True)

        with (
            patch.object(poller, "load_sources", return_value=[ATSSource("greenhouse", "stripe"), ATSSource("lever", "stripe")]),
            patch.object(poller, "fetch_jobs", side_effect=[greenhouse_jobs, lever_jobs]),
            patch.object(poller, "load_state", return_value=state),
            patch.object(poller, "save_state"),
            patch.object(poller, "score_job", score_job),
            patch.object(poller, "send_slack_alert", send_slack_alert),
            patch.object(poller, "send_new_jobs_digest", send_digest),
            patch.object(poller, "write_jobs_markdown"),
            patch.object(poller, "sleep_between_scores"),
            patch.object(poller.sys, "exit") as sys_exit,
        ):
            poller.main()

        self.assertEqual(score_job.call_count, 6)
        digest_jobs = send_digest.call_args.args[0]
        self.assertEqual(len(digest_jobs), 6)
        scored_ids = {job["id"] for job in digest_jobs}
        self.assertNotIn("g0", scored_ids)
        self.assertNotIn("l0", scored_ids)
        self.assertEqual(state["greenhouse:stripe:g0"]["alerted"], False)
        self.assertEqual(state["lever:stripe:l0"]["alerted"], False)
        self.assertEqual(len(state), 8)
        sys_exit.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
