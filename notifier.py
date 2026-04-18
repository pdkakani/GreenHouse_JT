"""
notifier.py — Slack webhook alerter for high-match jobs.
Accepts a plain int score from scorer.py.
"""

import os
import json
import requests
from typing import Union

SLACK_TIMEOUT = 10


def _get_webhook_url():
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    return url if url else None


def _score_emoji(score: int) -> str:
    if score >= 85:
        return "🔥"
    elif score >= 70:
        return "🎯"
    elif score >= 65:
        return "✅"
    else:
        return "👀"


def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def send_slack_alert(job: dict, score: int) -> bool:
    """Send a Slack alert for a high-match job. score is a plain int."""
    webhook_url = _get_webhook_url()
    if not webhook_url:
        print("  [slack] SLACK_WEBHOOK_URL not set — skipping alert.")
        return False

    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "Remote / Unspecified")
    url = job.get("_url", "")
    job_id = job.get("id", "")
    updated_at = job.get("updated_at", "")
    department = job.get("_department", "")

    emoji = _score_emoji(score)
    bar = _score_bar(score)
    dept_text = f" · {department}" if department else ""

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {score}% Match — {title}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Company*\n{company}{dept_text}"},
                {"type": "mrkdwn", "text": f"*Location*\n{location}"},
                {"type": "mrkdwn", "text": f"*Score*\n`{bar}` {score}/100"},
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔗 Apply Now", "emoji": True},
                    "url": url,
                    "style": "primary",
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Job ID: `{job_id}` · Updated: `{updated_at}`"}
            ],
        },
        {"type": "divider"},
    ]

    payload = {
        "text": f"{emoji} {score}% Match: {title} @ {company}",
        "blocks": blocks,
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"  [slack] ✅ Alert sent: {title} @ {company} ({score}%)")
            return True
        else:
            print(f"  [slack] ❌ Failed ({resp.status_code}): {resp.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [slack] ❌ Request error: {e}")
        return False



def send_new_jobs_digest(new_jobs: list[dict]) -> bool:
    """
    Send a single Slack message listing ALL new jobs found in a run.
    Sent regardless of score — this is a full digest, not a match alert.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url or not new_jobs:
        return False

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"\U0001f195 {len(new_jobs)} New Job{'s' if len(new_jobs) != 1 else ''} Found",
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]

    for job in new_jobs:
        title = job.get("title", "Unknown Title")
        company = job.get("company", "Unknown Company")
        location = job.get("_location", "Remote / Unspecified")
        url = job.get("_url", "")
        score = job.get("_score")
        score_text = f" · \U0001f3af {score}%" if score is not None else ""

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{url}|{title}>*\n{company} · {location}{score_text}",
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Apply", "emoji": True},
                "url": url,
            },
        })

    blocks.append({"type": "divider"})

    payload = {
        "text": f"\U0001f195 {len(new_jobs)} new job(s) found",
        "blocks": blocks,
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"  [slack] \u2705 New jobs digest sent ({len(new_jobs)} jobs)")
            return True
        else:
            print(f"  [slack] \u274c Digest failed ({resp.status_code}): {resp.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [slack] \u274c Digest request error: {e}")
        return False

def send_run_summary(stats: dict) -> bool:
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return False

    payload = {
        "text": (
            f"📊 *Job Poll Summary* — "
            f"{stats.get('jobs_new', 0)} new · "
            f"{stats.get('jobs_updated', 0)} updated · "
            f"{stats.get('alerts_sent', 0)} alerts sent · "
            f"{stats.get('elapsed', 0)}s elapsed"
        )
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        return resp.status_code == 200
    except Exception:
        return False