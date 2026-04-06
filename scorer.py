"""
scorer.py — Resume match scoring using Claude API.

Sends job title + description + resume to Claude and gets back
a match score (0-100) with bullet-point reasoning.

API key is read from the CL_API_KEY environment variable
(set as a GitHub Actions secret).
"""

import os
import json
import time
import requests
from pathlib import Path
from typing import Optional

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
SCORE_THRESHOLD = 50        # only alert if score >= this
MAX_RETRIES = 2
RETRY_DELAY = 5             # seconds

RESUME_FILE = Path("resume.txt")
_resume_cache: Optional[str] = None


def _load_resume() -> str:
    global _resume_cache
    if _resume_cache is None:
        if not RESUME_FILE.exists():
            raise FileNotFoundError(f"resume.txt not found at {RESUME_FILE.absolute()}")
        _resume_cache = RESUME_FILE.read_text(encoding="utf-8").strip()
    return _resume_cache


def _get_api_key() -> str:
    key = os.environ.get("CL_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("CL_API_KEY environment variable is not set.")
    return key


def _build_prompt(job_title: str, company: str, location: str, description: str) -> str:
    resume = _load_resume()
    # Truncate description if very long (stay well within token limits)
    if len(description) > 6000:
        description = description[:6000] + "\n... [truncated]"

    return f"""You are a strict but fair technical recruiter evaluating how well a candidate's resume matches a job posting.

<resume>
{resume}
</resume>

<job>
Title: {job_title}
Company: {company}
Location: {location}
Description:
{description}
</job>

Evaluate the match and respond ONLY with a JSON object in this exact format (no markdown, no preamble):
{{
  "score": <integer 0-100>,
  "verdict": "<one line summary, max 12 words>",
  "reasons": [
    "<specific match or gap, max 15 words>",
    "<specific match or gap, max 15 words>",
    "<specific match or gap, max 15 words>"
  ]
}}

Scoring guide:
- 80-100: Strong match — core skills, domain, and seniority align well
- 60-79:  Good match — most requirements met, minor gaps
- 40-59:  Partial match — relevant background but meaningful gaps
- 20-39:  Weak match — some transferable skills, but significantly misaligned
- 0-19:   Poor match — little overlap

Be specific. Reference actual skills and technologies from both the resume and job description.
Do not be generous — score what genuinely matches, not potential."""


def score_job(job: dict) -> Optional[dict]:
    """
    Score a job against the resume.
    Returns dict with score, verdict, reasons — or None on failure.
    """
    title = job.get("title", "")
    company = job.get("company", "")
    location = job.get("_location", "")

    # Extract description from Greenhouse job object
    content = job.get("content", "") or ""
    # Greenhouse wraps description in HTML — strip tags for cleaner scoring
    description = _strip_html(content)

    if not description:
        # No description available — score based on title/company only
        description = f"(No description available. Title: {title})"

    prompt = _build_prompt(title, company, location, description)

    headers = {
        "x-api-key": _get_api_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["content"][0]["text"].strip()
                # Strip accidental markdown fences
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                result = json.loads(raw_text)
                # Validate shape
                if "score" in result and "verdict" in result and "reasons" in result:
                    result["score"] = max(0, min(100, int(result["score"])))
                    return result
                else:
                    print(f"  [scorer] Unexpected response shape: {raw_text[:200]}")
                    return None

            elif resp.status_code == 429:
                print(f"  [scorer] Rate limited (attempt {attempt}), waiting {RETRY_DELAY*2}s...")
                time.sleep(RETRY_DELAY * 2)

            elif resp.status_code in (500, 529):
                print(f"  [scorer] API overloaded (attempt {attempt}), waiting {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

            else:
                print(f"  [scorer] API error {resp.status_code}: {resp.text[:200]}")
                return None

        except json.JSONDecodeError as e:
            print(f"  [scorer] JSON parse error: {e}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"  [scorer] Request error (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    return None


def should_alert(score: int) -> bool:
    return score >= SCORE_THRESHOLD


def _strip_html(html: str) -> str:
    """Very lightweight HTML tag stripper — no external deps needed."""
    import re
    # Replace block tags with newlines
    html = re.sub(r"<(br|p|li|tr|div|h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Remove all remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # Decode common HTML entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse excessive whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)
    return html.strip()
