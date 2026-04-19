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


# def send_slack_alert(job: dict, score: int) -> bool:
#     """Send a Slack alert for a single high-match job (score >= 65)."""
#     webhook_url = _get_webhook_url()
#     if not webhook_url:
#         print("  [slack] SLACK_WEBHOOK_URL not set — skipping alert.")
#         return False
#
#     title = job.get("title", "Unknown Title")
#     company = job.get("company", "Unknown Company")
#     location = job.get("_location", "Remote / Unspecified")
#     url = job.get("_url", "")
#     job_id = job.get("id", "")
#     updated_at = job.get("updated_at", "")
#     department = job.get("_department", "")
#
#     emoji = _score_emoji(score)
#     bar = _score_bar(score)
#     dept_text = f" · {department}" if department else ""
#
#     blocks = [
#         {
#             "type": "header",
#             "text": {
#                 "type": "plain_text",
#                 "text": f"{emoji} {score}% Match — {title}",
#                 "emoji": True,
#             },
#         },
#         {
#             "type": "section",
#             "fields": [
#                 {"type": "mrkdwn", "text": f"*Company*\n{company}{dept_text}"},
#                 {"type": "mrkdwn", "text": f"*Location*\n{location}"},
#                 {"type": "mrkdwn", "text": f"*Score*\n`{bar}` {score}/100"},
#             ],
#         },
#         {
#             "type": "actions",
#             "elements": [
#                 {
#                     "type": "button",
#                     "text": {"type": "plain_text", "text": "🔗 Apply Now", "emoji": True},
#                     "url": url,
#                     "style": "primary",
#                 }
#             ],
#         },
#         {
#             "type": "context",
#             "elements": [
#                 {"type": "mrkdwn", "text": f"Job ID: `{job_id}` · Updated: `{updated_at}`"}
#             ],
#         },
#         {"type": "divider"},
#     ]
#
#     payload = {
#         "text": f"{emoji} {score}% Match: {title} @ {company}",
#         "blocks": blocks,
#     }
#
#     try:
#         resp = requests.post(
#             webhook_url,
#             data=json.dumps(payload),
#             headers={"Content-Type": "application/json"},
#             timeout=SLACK_TIMEOUT,
#         )
#         if resp.status_code == 200:
#             print(f"  [slack] ✅ Alert sent: {title} @ {company} ({score}%)")
#             return True
#         else:
#             print(f"  [slack] ❌ Failed ({resp.status_code}): {resp.text[:100]}")
#             return False
#     except requests.exceptions.RequestException as e:
#         print(f"  [slack] ❌ Request error: {e}")
#         return False


def send_new_jobs_digest(new_jobs: list[dict], stats: dict) -> bool:
    """
    Send a single Slack message with:
      - top 20 newest jobs found this run (overflow count noted)
      - run summary stats at the bottom

    All jobs are already persisted in seen_jobs.json before this is called,
    so the digest is purely informational — nothing is lost on overflow.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        print("  [slack] SLACK_WEBHOOK_URL not set — skipping digest.")
        return False
    if not new_jobs:
        return False

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(new_jobs)
    MAX_IN_DIGEST = 20

    # Sort newest-first so overflow always drops the oldest, not the newest
    sorted_jobs = sorted(new_jobs, key=lambda j: j.get("updated_at", ""), reverse=True)
    shown = sorted_jobs[:MAX_IN_DIGEST]
    overflow = total - len(shown)

    lines = []
    for job in shown:
        title = job.get("title", "Unknown Title")
        company = job.get("company", "Unknown Company")
        location = job.get("_location", "")
        url = job.get("_url", "")
        loc_part = f" · {location}" if location else ""
        lines.append(f"• 🆕 <{url}|{title}> @ {company}{loc_part}")

    if overflow > 0:
        lines.append(f"\n_…and {overflow} more recorded in seen_jobs.json_")

    # ── Run summary line ──────────────────────────────────────────────────────
    summary = (
        f"📊 *Run Summary* — "
        f"{stats.get('companies_checked', 0)}/{stats.get('companies_checked', 0) + stats.get('companies_failed', 0)} companies · "
        f"{stats.get('jobs_fetched', 0)} fetched · "
        f"{stats.get('jobs_new', 0)} new 🆕 · "
        f"{stats.get('jobs_updated', 0)} updated 🔄 · "
        f"{stats.get('jobs_skipped_location', 0)} loc filtered · "
        f"{stats.get('jobs_skipped_title', 0)} title filtered · "
        f"{stats.get('elapsed', 0)}s"
    )

    header = f"🆕 *{total} New Job{'s' if total != 1 else ''} Found* — {timestamp}"
    text = header + "\n" + "\n".join(lines) + "\n\n" + summary

    payload = {
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"  [slack] ✅ Digest sent ({len(shown)} shown, {overflow} overflow)")
            return True
        else:
            print(f"  [slack] ❌ Failed ({resp.status_code}): {resp.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [slack] ❌ Request error: {e}")
        return False