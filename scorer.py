"""
scorer.py — Resume match scoring using Groq API (free tier).

Model: llama-3.3-70b-versatile — best free model on Groq for reasoning tasks.
Free tier: 14,400 requests/day — more than sufficient for job scoring.
Cost: $0.00

API key stored as GitHub secret: GROQ_API_KEY
"""

import os
import re
import time
import requests
from pathlib import Path
from typing import Optional

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"
SCORE_THRESHOLD = 65
MAX_RETRIES = 2
RETRY_DELAY = 5

RESUME_FILE = Path("resume.txt")
_resume_cache: Optional[str] = None

MAX_RESUME_CHARS = 1500       # ~375 tokens
MAX_DESCRIPTION_CHARS = 2000  # ~500 tokens


def _load_resume() -> str:
    global _resume_cache
    if _resume_cache is None:
        if not RESUME_FILE.exists():
            raise FileNotFoundError(f"resume.txt not found at {RESUME_FILE.absolute()}")
        _resume_cache = RESUME_FILE.read_text(encoding="utf-8").strip()[:MAX_RESUME_CHARS]
    return _resume_cache


def _get_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return key


def _build_prompt(job_title: str, company: str, description: str) -> str:
    resume = _load_resume()
    desc = description[:MAX_DESCRIPTION_CHARS]
    return f"""Score how well this resume matches the job. Reply with a single integer from 0 to 100. Nothing else. No explanation.

RESUME:
{resume}

JOB: {job_title} at {company}
{desc}"""


def score_job(job: dict) -> Optional[int]:
    """Returns a score (0-100) or None on failure."""
    title = job.get("title", "")
    company = job.get("company", "")
    content = job.get("content", "") or ""
    description = _strip_html(content)

    if not description:
        description = f"No description. Title: {title}"

    prompt = _build_prompt(title, company, description)

    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 5,        # a number 0-100 needs at most 3 tokens
        "temperature": 0,       # deterministic — no randomness for scoring
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                GROQ_API_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["choices"][0]["message"]["content"].strip()
                usage = data.get("usage", {})
                print(f"  [scorer] tokens: {usage.get('prompt_tokens','?')} in / {usage.get('completion_tokens','?')} out")

                match = re.search(r'\b(\d{1,3})\b', raw_text)
                if match:
                    score = max(0, min(100, int(match.group(1))))
                    return score
                else:
                    print(f"  [scorer] Unexpected response: {raw_text[:50]}")
                    return None

            elif resp.status_code == 429:
                wait = RETRY_DELAY * 2
                print(f"  [scorer] Rate limited (attempt {attempt}), waiting {wait}s...")
                time.sleep(wait)

            elif resp.status_code in (500, 503):
                print(f"  [scorer] API overloaded (attempt {attempt}), waiting {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

            else:
                print(f"  [scorer] API error {resp.status_code}: {resp.text[:200]}")
                return None

        except requests.exceptions.RequestException as e:
            print(f"  [scorer] Request error (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    return None


def should_alert(score: int) -> bool:
    return score >= SCORE_THRESHOLD


def _strip_html(html: str) -> str:
    html = re.sub(r"<(br|p|li|tr|div|h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)
    return html.strip()