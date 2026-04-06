"""
notifier.py — Slack webhook alerter for high-match jobs.

Sends a richly formatted Slack message when a job scores >= threshold.
Webhook URL is read from SLACK_WEBHOOK_URL environment variable
(set as a GitHub Actions secret).
"""

import os
import json
import requests
from typing import Optional

SLACK_TIMEOUT = 10


def _get_webhook_url() -> Optional[str]:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    return url if url else None


def _score_emoji(score: int) -> str:
    if score >= 85:
        return "🔥"
    elif score >= 70:
        return "🎯"
    elif score >= 60:
        return "✅"
    else:
        return "👀"


def _score_bar(score: int) -> str:
    """Visual progress bar for the score."""
    filled = round(score / 10)
    empty = 10 - filled
    return "█" * filled + "░" * empty


def send_slack_alert(job: dict, score_result: dict) -> bool:
    """
    Send a Slack alert for a high-match job.
    Returns True on success, False on failure.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        print("  [slack] SLACK_WEBHOOK_URL not set — skipping alert.")
        return False

    score = score_result.get("score", 0)
    verdict = score_result.get("verdict", "")
    reasons = score_result.get("reasons", [])

    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "Remote / Unspecified")
    url = job.get("_url", "")
    job_id = job.get("id", "")
    updated_at = job.get("updated_at", "")
    tag = job.get("_tag", "NEW")
    department = job.get("_department", "")

    tag_label = "🆕 New" if tag == "NEW" else "🔄 Updated"
    emoji = _score_emoji(score)
    bar = _score_bar(score)

    dept_text = f" · {department}" if department else ""

    # Format reasons as bullet points
    reasons_text = "\n".join(f"• {r}" for r in reasons) if reasons else "• No details available"

    # Build Slack Block Kit message
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
                {
                    "type": "mrkdwn",
                    "text": f"*Company*\n{company}{dept_text}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Location*\n{location}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Status*\n{tag_label}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Score*\n`{bar}` {score}/100",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Verdict:* _{verdict}_\n\n*Why it matches:*\n{reasons_text}",
            },
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
                {
                    "type": "mrkdwn",
                    "text": f"Job ID: `{job_id}` · Updated: `{updated_at}`",
                }
            ],
        },
        {"type": "divider"},
    ]

    payload = {
        "text": f"{emoji} {score}% Match: {title} @ {company}",  # fallback for notifications
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


def send_run_summary(stats: dict) -> bool:
    """
    Send a brief run summary to Slack.
    Only sent if at least one alert was fired this run.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return False

    payload = {
        "text": (
            f"📊 *Job Poll Summary* — "
            f"{stats.get('jobs_new', 0)} new · "
            f"{stats.get('jobs_updated', 0)} updated · "
            f"{stats.get('alerts_sent', 0)} alerts sent · "
            f"{stats.get('elapsed', 0):.1f}s elapsed"
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
