"""
notifier.py — Slack webhook alerter for high-match jobs.

send_slack_alert     : individual alert for a single high-scoring job (>= 65%)
send_new_jobs_digest : one plain-text summary per run for ALL new jobs
send_run_summary     : end-of-run stats summary
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
    """Send a Slack alert for a single high-match job (score >= 65)."""
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
    Send a single plain-text Slack message summarising ALL new jobs found
    in a run. Uses plain text (no blocks) to avoid Slack's 50-block limit
    when hundreds of jobs are found.

    Format:
        🆕 142 New Jobs Found — 2026-04-19 00:30 UTC
        • Software Engineer @ Stripe · Remote, USA
        • Backend Engineer @ Coinbase · New York, NY
        ...
        (and 100 more — check output/jobs.md for the full list)
    """
    webhook_url = _get_webhook_url()
    if not webhook_url or not new_jobs:
        return False

    MAX_LISTED = 40  # list up to this many jobs inline; summarise the rest

    lines = []
    for job in new_jobs[:MAX_LISTED]:
        title = job.get("title", "Unknown Title")
        company = job.get("company", "Unknown Company")
        location = job.get("_location", "")
        url = job.get("_url", "")
        loc_part = f" · {location}" if location else ""
        # Slack mrkdwn link inside plain text block
        lines.append(f"• <{url}|{title}> @ {company}{loc_part}")

    remainder = len(new_jobs) - MAX_LISTED
    if remainder > 0:
        lines.append(f"_...and {remainder} more — see output/jobs.md for the full list_")

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"🆕 *{len(new_jobs)} New Job{'s' if len(new_jobs) != 1 else ''} Found* — {timestamp}"

    text = header + "\n" + "\n".join(lines)

    payload = {
        "text": text,
        # Single mrkdwn block — never hits the 50-block limit
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            }
        ],
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"  [slack] ✅ New jobs digest sent ({len(new_jobs)} jobs)")
            return True
        else:
            print(f"  [slack] ❌ Digest failed ({resp.status_code}): {resp.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [slack] ❌ Digest request error: {e}")
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
            f"{stats.get('scores_completed', 0)} scored · "
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