"""
scorer.py — Resume match scoring using Google's Gemini 2.5 Flash-Lite.

Model choice:
  - gemini-2.5-flash-lite: free-tier friendly and sufficient for job/resume
    matching at this repo's scale.

API key stored as GitHub secret: GEMINI_API_KEY
"""

import json
import os
import re
import time
import requests
from pathlib import Path
from typing import Optional

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
MODEL = "gemini-2.5-flash-lite"
SCORE_THRESHOLD = 65
RETRY_DELAY = 5
SCORE_SPACING_SECONDS = 4

RESUME_FILE = Path("resume.txt")
_resume_cache: Optional[str] = None

MAX_JD_CHARS = 12000
MAX_RESUME_CHARS = 8000


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
Internally evaluate each criterion, then output ONLY a JSON object with a single key:
{{"score": <integer from 0 to 100>}}
Do not output anything else — no explanation, no markdown, no extra keys.

STRICTNESS RULES (must follow before final score):
- Start from a conservative baseline of 30 and add points only for clear evidence.
- Penalize vague or transferable experience unless explicitly relevant.
- If role family is meaningfully different from resume background, cap score at 45.
- If seniority mismatch is 2+ levels (e.g. junior vs staff), cap score at 60.
- If 2 or more core required skills are missing, cap score at 55.
- Do NOT give scores above 80 unless there is strong alignment across all 4 criteria.
- Prefer false negatives over false positives.

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
"""


def _build_request_payload(prompt: str) -> dict:
    # Ask Gemini for machine-readable JSON so score parsing stays deterministic.
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt,
                    }
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    }
                },
                "required": ["score"],
                "additionalProperties": False,
            },
            "maxOutputTokens": 32,
            "temperature": 0.0,
        },
    }


def _extract_output_text(data: dict) -> str:
    # Gemini may return the model text in a couple of slightly different
    # response shapes depending on SDK/version, so we normalize them here.
    text = data.get("output_text", "")
    if text:
        return text.strip()

    text = data.get("text", "")
    if text:
        return text.strip()

    candidates = data.get("candidates", [])
    if not candidates:
        return ""

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    text_parts = [part.get("text", "").strip() for part in parts if part.get("text")]
    return "".join(text_parts).strip()


def _extract_score(raw_text: str) -> Optional[int]:
    raw_text = raw_text.strip()
    if not raw_text:
        return None

    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            value = data.get("score")
            if isinstance(value, int):
                return max(0, min(100, value))
            if isinstance(value, str) and value.isdigit():
                return max(0, min(100, int(value)))
    except json.JSONDecodeError:
        pass

    match = re.search(r"\b(\d{1,3})\b", raw_text)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


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

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    payload = _build_request_payload(prompt)
    url = GEMINI_API_URL.format(model=MODEL)

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            raw_text = _extract_output_text(data)
            usage = data.get("usageMetadata", {})
            inp = usage.get("promptTokenCount", "?")
            out = usage.get("candidatesTokenCount", "?")
            print(f"  [scorer] tokens: {inp} in / {out} out")

            score = _extract_score(raw_text)
            if score is not None:
                return score

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
            payload = _build_request_payload(short_prompt)
            try:
                resp2 = requests.post(url, json=payload, headers=headers, timeout=30)
                if resp2.status_code == 200:
                    data = resp2.json()
                    raw_text = _extract_output_text(data)
                    score = _extract_score(raw_text)
                    if score is not None:
                        return score
                print(f"  [scorer] 413 retry failed ({resp2.status_code}) — skipping.")
            except requests.exceptions.RequestException:
                pass
            return None

        elif resp.status_code in (500, 503):
            print(f"  [scorer] API overloaded ({resp.status_code}), waiting {RETRY_DELAY}s then retrying once...")
            time.sleep(RETRY_DELAY)
            try:
                resp2 = requests.post(url, json=payload, headers=headers, timeout=30)
                if resp2.status_code == 200:
                    data = resp2.json()
                    raw_text = _extract_output_text(data)
                    score = _extract_score(raw_text)
                    if score is not None:
                        return score
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


def sleep_between_scores() -> None:
    time.sleep(SCORE_SPACING_SECONDS)


def _strip_html(html: str) -> str:
    html = re.sub(r"<(br|p|li|tr|div|h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)
    return html.strip()
