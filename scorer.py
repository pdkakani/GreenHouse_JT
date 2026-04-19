"""
scorer.py — Resume match scoring using Google Gemini API (free tier).

Model: gemini-2.5-flash-lite  (fastest, highest throughput on free tier)
Free tier limits:
  - 15 requests/minute
  - 1,000 requests/day
  - 250,000 tokens/minute  ← 40x more than Groq free tier

At ~3,000 tokens/request and 4s sleep between calls:
  - ~15 req/min = well within RPM limit
  - ~45,000 tokens/min = well within TPM limit
  - 1,000 RPD = ~96 scoring runs/day × ~10 jobs = fine for steady state

API key stored as GitHub secret: GEMINI_API_KEY
Get key: aistudio.google.com → Get API key (no credit card needed)
"""

import os
import re
import time
import requests
from pathlib import Path
from typing import Optional

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)
MODEL = "gemini-2.5-flash"  # stable, available now, 15 RPM / 250K TPM on free tier
SCORE_THRESHOLD = 65
RETRY_DELAY = 5

RESUME_FILE = Path("resume.txt")
_resume_cache: Optional[str] = None

MAX_JD_CHARS = 8000    # ~2000 tokens — captures full JD requirements
MAX_RESUME_CHARS = 3000  # ~750 tokens — full resume comfortably


def _load_resume() -> str:
    global _resume_cache
    if _resume_cache is None:
        if not RESUME_FILE.exists():
            raise FileNotFoundError(f"resume.txt not found at {RESUME_FILE.absolute()}")
        _resume_cache = RESUME_FILE.read_text(encoding="utf-8").strip()
    return _resume_cache


def _get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    return key


def _build_prompt(job_title: str, company: str, description: str) -> str:
    resume = _load_resume()[:MAX_RESUME_CHARS]
    desc = description[:MAX_JD_CHARS]
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
{desc}

Final score (0-100 integer only):"""


def score_job(job: dict) -> Optional[int]:
    """Returns a score (0-100) or None on failure."""
    title = job.get("title", "")
    company = job.get("company", "")
    content = job.get("content", "") or ""
    description = _strip_html(content)

    if not description:
        description = f"No description available. Evaluate based on title only: {title}"

    prompt = _build_prompt(title, company, description)
    api_key = _get_api_key()

    url = GEMINI_API_URL.format(model=MODEL, api_key=api_key)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 10,
            "temperature": 0.0,
        },
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            raw_text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
            # Log token usage
            usage = data.get("usageMetadata", {})
            inp = usage.get("promptTokenCount", "?")
            out = usage.get("candidatesTokenCount", "?")
            print(f"  [scorer] tokens: {inp} in / {out} out")

            match = re.search(r'\b(\d{1,3})\b', raw_text)
            if match:
                return max(0, min(100, int(match.group(1))))
            else:
                print(f"  [scorer] Unexpected response: {raw_text[:50]}")
                return None

        elif resp.status_code == 429:
            print(f"  [scorer] Rate limited (429) — skipping, will retry next run.")
            return None

        elif resp.status_code == 413:
            # Payload too large — retry with shorter JD
            print(f"  [scorer] 413 payload too large — retrying with shorter JD...")
            short_desc = description[:MAX_JD_CHARS // 2]
            short_prompt = _build_prompt(title, company, short_desc)
            payload["contents"][0]["parts"][0]["text"] = short_prompt
            try:
                resp2 = requests.post(url, json=payload, timeout=30)
                if resp2.status_code == 200:
                    data = resp2.json()
                    raw_text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                        .strip()
                    )
                    match = re.search(r'(\d{1,3})', raw_text)
                    if match:
                        return max(0, min(100, int(match.group(1))))
                print(f"  [scorer] 413 retry failed ({resp2.status_code}) — skipping.")
            except requests.exceptions.RequestException:
                pass
            return None

        elif resp.status_code in (500, 503):
            print(f"  [scorer] API overloaded ({resp.status_code}), waiting {RETRY_DELAY}s then retrying once...")
            time.sleep(RETRY_DELAY)
            try:
                resp2 = requests.post(url, json=payload, timeout=30)
                if resp2.status_code == 200:
                    data = resp2.json()
                    raw_text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                        .strip()
                    )
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