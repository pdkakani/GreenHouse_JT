import os
import unittest
from unittest.mock import Mock, patch

import scorer


class ScorerTests(unittest.TestCase):
    def test_extract_score_from_json(self):
        self.assertEqual(scorer._extract_score('{"score": 87}'), 87)

    def test_extract_score_from_stringified_json(self):
        self.assertEqual(scorer._extract_score('{"score": "42"}'), 42)

    def test_extract_score_falls_back_to_numeric_text(self):
        self.assertEqual(scorer._extract_score("score: 91"), 91)

    def test_extract_output_text_prefers_direct_field(self):
        data = {"text": "  73  "}
        self.assertEqual(scorer._extract_output_text(data), "73")

    def test_extract_output_text_reads_nested_output(self):
        data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"score": 64}'},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(scorer._extract_output_text(data), '{"score": 64}')

    def test_build_prompt_contains_json_contract(self):
        prompt = scorer._build_prompt("Backend Engineer", "Acme", "Build APIs")
        self.assertIn('output ONLY a JSON object', prompt)
        self.assertIn('"score": <integer from 0 to 100>', prompt)

    def test_score_job_sends_structured_openai_payload(self):
        job = {
            "title": "Backend Engineer",
            "company": "Acme",
            "content": "<p>Build APIs</p>",
        }
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"score": 77}'},
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 123, "candidatesTokenCount": 4},
        }

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(scorer, "_load_resume", return_value="RESUME"),
            patch.object(scorer.requests, "post", return_value=response) as post,
        ):
            score = scorer.score_job(job)

        self.assertEqual(score, 77)
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-key")
        self.assertEqual(kwargs["json"]["contents"][0]["role"], "user")
        self.assertEqual(kwargs["json"]["contents"][0]["parts"][0]["text"].startswith("You are a strict technical recruiter"), True)
        self.assertIn("Backend Engineer", kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertIn("RESUME", kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(kwargs["json"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(kwargs["json"]["generationConfig"]["responseJsonSchema"]["properties"]["score"]["minimum"], 0)
        self.assertEqual(kwargs["json"]["generationConfig"]["responseJsonSchema"]["properties"]["score"]["maximum"], 100)
        self.assertEqual(kwargs["json"]["generationConfig"]["maxOutputTokens"], 32)
        self.assertEqual(kwargs["json"]["generationConfig"]["temperature"], 0.0)

    def test_score_job_retries_413_with_structured_payload(self):
        job = {
            "title": "Backend Engineer",
            "company": "Acme",
            "content": "<p>" + ("Build APIs. " * 2000) + "</p>",
        }
        first = Mock()
        first.status_code = 413
        first.text = "payload too large"

        second = Mock()
        second.status_code = 200
        second.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"score": 61}'},
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 456, "candidatesTokenCount": 4},
        }

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(scorer, "_load_resume", return_value="RESUME"),
            patch.object(scorer.requests, "post", side_effect=[first, second]) as post,
        ):
            score = scorer.score_job(job)

        self.assertEqual(score, 61)
        self.assertEqual(post.call_count, 2)
        first_kwargs = post.call_args_list[0].kwargs
        second_kwargs = post.call_args_list[1].kwargs
        self.assertEqual(first_kwargs["json"]["contents"][0]["role"], "user")
        self.assertEqual(second_kwargs["json"]["contents"][0]["role"], "user")
        self.assertEqual(second_kwargs["json"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("Backend Engineer", second_kwargs["json"]["contents"][0]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
