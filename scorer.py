"""
scorer.py — Resume match scoring using Groq API (free tier).

Model: llama-3.3-70b-versatile
Full resume + full JD sent — no truncation.
Structured 4-criteria prompt for accurate, consistent scoring.
Cost: $0.00 (free tier)

Retry philosophy: fail fast — the persistent queue handles retries across runs.
One attempt only. If it fails, the job stays in the queue for next run.
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
MAX_RETRIES = 1          # fail fast — queue retries next run
RETRY_DELAY = 5          # only used on transient 500/503 errors
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
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return key


def _build_prompt(job_title: str, company: str, description: str) -> str:
    resume = _load_resume()
    return f"""You are a strict technical recruiter evaluating resume-to-job fit.

Score this resume against the job using the 4 criteria below.

Internally evaluate each criterion, then output ONLY a single integer from 0 to 100 as your final weighted score.

Do not output anything else — no explanation, no breakdown, just the integer.

SCORING CRITERIA (evaluate internally before giving final score):
1. Technical skills match — languages, frameworks, tools, cloud (weight: 40%)
2. Domain and industry fit — fintech, banking, distributed systems, microservices (weight: 25%)
3. Seniority level match — junior/mid/senior/staff alignment (weight: 20%)
4. Years of experience alignment — required vs actual (weight: 15%)

RESUME:
{resume}

JOB TITLE: {job_title}
COMPANY: {company}

JOB DESCRIPTION:
{description}

Final score (0-100 integer only):"""


def score_job(job: dict) -> Optional[int]:
    """
    Returns a score (0-100) or None on failure.
    Fails fast — no aggressive retries. The queue handles retries across runs.
    """
    title = job.get("title", "")
    company = job.get("company", "")
    content = job.get("content", "") or ""
    description = _strip_html(content)

    if not description:
        description = f"No description available. Evaluate based on title only: {title}"

    prompt = _build_prompt(title, company, description)

    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 20,
        "temperature": 0,
    }

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
                return max(0, min(100, int(match.group(1))))
            else:
                print(f"  [scorer] Unexpected response: {raw_text[:50]}")
                return None

        elif resp.status_code == 429:
            # Rate limited — don't wait, just skip. Queue retries next run.
            print(f"  [scorer] Rate limited (429) — skipping, will retry next run.")
            return None

        elif resp.status_code in (500, 503):
            # One short retry on server errors only
            print(f"  [scorer] API overloaded ({resp.status_code}), waiting {RETRY_DELAY}s then retrying once...")
            time.sleep(RETRY_DELAY)
            try:
                resp2 = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
                if resp2.status_code == 200:
                    data = resp2.json()
                    raw_text = data["choices"][0]["message"]["content"].strip()
                    match = re.search(r'\b(\d{1,3})\b', raw_text)
                    if match:
                        return max(0, min(100, int(match.group(1))))
                print(f"  [scorer] Retry failed ({resp2.status_code}) — skipping.")
            except requests.exceptions.RequestException:
                pass
            return None

        else:
            print(f"  [scorer] API error {resp.status_code}: {resp.text[:200]}")
            return None

    except requests.exceptions.Timeout:
        print(f"  [scorer] Request timed out — skipping, will retry next run.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  [scorer] Request error: {e} — skipping, will retry next run.")
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